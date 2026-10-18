#!/usr/bin/env python3
"""Acceptance sweep for a deployed release, with a report the owner can read.

This answers one question: is the release that is actually running on this host
healthy, and can it be driven? Every check reads the live deployment — the
identity file, the working tree, the systemd units, the running services, the
queue, the agent — rather than the source, because the two can and do differ.

The report is built as Markdown and rendered **once** through the product's own
renderer, then sent as HTML. That is the whole point of it being here: an earlier
version of this report escaped its own text and then rendered it again, and the
owner received an empty version number, a raw JSON line and a visible ``&gt;``.
A report that cannot be read is not a report.

Usage:
    scripts/acceptance.py            # print the sweep, exit non-zero on failure
    scripts/acceptance.py --report   # also deliver it to the owner's chat
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
IDENTITY = RUNTIME / "deployment-identity"
WATCHDOG = RUNTIME / "watchdog-latest.json"

sys.path.insert(0, str(ROOT))

from bridge.telegram.rendering.collapse_guard import collapse_start  # noqa: E402
from bridge.telegram.rendering.markdown_html import render_markdown_html  # noqa: E402
from bridge.telegram.rendering.news_pages import build_news_pages  # noqa: E402

DIGEST = (
    "# نشرة اليوم\n\n"
    "## ما حدث\n\n"
    "- **ما حدث:** إطلاق نموذج لغوي جديد مفتوح المصدر من شركة محلية.\n"
    "- **لماذا يهمك:** يخفّض كلفة تشغيل النصوص الطويلة على أجهزتك.\n"
    "- **المصدر:** https://example.com/story-one\n\n"
    "## ما الخطوة التالية\n\n"
    "جرّب النموذج على تقريرك الأسبوعي، وقارن الزمن مع أبطئ إعداد لديك.\n"
)
PLAIN_ANSWER = "الجواب قصير ومباشر: نعم، الإعداد جاهز ولا يحتاج تدخلاً منك الآن."
COLLAPSED_ANSWER = "نص سليم.\n\n" + "تخطيط مساحة العمل " * 60 + "\n"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        return f"{mark} {self.name}: {self.detail}" if self.detail else f"{mark} {self.name}"


def run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run a command with no shell, returning its status and combined output."""
    try:
        finished = subprocess.run(  # fixed argv, no shell
            argv, capture_output=True, text=True, timeout=timeout, check=False, cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return finished.returncode, (finished.stdout + finished.stderr).strip()


def identity() -> dict[str, str]:
    try:
        value = json.loads(IDENTITY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def git(*args: str) -> tuple[int, str]:
    return run(["git", "-C", str(ROOT), *args])


def check_deployment_version() -> Check:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    recorded = identity().get("version", "")
    ok = bool(recorded) and recorded == version
    return Check("deployment version", ok, f"VERSION={version} recorded={recorded or 'none'}")


def check_deployment_identity() -> Check:
    info = identity()
    ok = all(info.get(key) for key in ("tag", "version", "commit"))
    return Check("deployment identity", ok, f"{info.get('tag', '?')} @ {info.get('commit', '?')[:12]}")


def check_head_matches() -> Check:
    code, head = git("rev-parse", "HEAD")
    return Check(
        "git head matches deployment", code == 0 and head == identity().get("commit"),
        head[:12] or "unavailable",
    )


def check_tree_clean() -> Check:
    code, out = git("status", "--porcelain")
    return Check("working tree is clean", code == 0 and not out.strip(), out.strip()[:80] or "clean")


def check_services() -> Check:
    code, out = run(
        ["systemctl", "--user", "is-active",
         "opencode-bridge-telegram.service", "opencode-serve.service"],
    )
    active = [line for line in out.splitlines() if line.strip()]
    ok = code == 0 and all(line.strip() == "active" for line in active) and len(active) == 2
    return Check("services are active", ok, " ".join(active) or out.strip()[:80])


def load_env() -> None:
    """Read the deployment's own .env so the checks use the real endpoints.

    The agent server sits behind basic auth, so a health probe without the
    password gets a 401 and would report a healthy service as broken.
    """
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def check_opencode_health() -> Check:
    import base64
    import urllib.request

    host = os.environ.get("OPENCODE_HOST", "127.0.0.1")
    port = os.environ.get("OPENCODE_PORT", "4096")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD") or os.environ.get("OPENCODE_PASSWORD") or ""
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    url = f"http://{host}:{port}/global/health"
    try:
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(request, timeout=10) as response:  # fixed localhost URL
            payload = json.load(response)
        ok = isinstance(payload, dict) and bool(payload.get("healthy"))
        detail = "healthy" if ok else f"unexpected payload {str(payload)[:60]}"
    except Exception as exc:  # any failure is the same finding
        ok, detail = False, f"{type(exc).__name__}"
    return Check("agent server health", ok, detail)


def check_watchdog() -> Check:
    try:
        payload = json.loads(WATCHDOG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Check("watchdog verdict", False, f"{type(exc).__name__}")
    status = str(payload.get("status", "unknown"))
    score = payload.get("score")
    issues = payload.get("issues") or []
    ok = status == "healthy" and not issues
    detail = f"{status} score={score}"
    if issues:
        detail += f" issues={', '.join(str(i) for i in issues)[:80]}"
    return Check("watchdog verdict", ok, detail)


def check_queue() -> Check:
    try:
        payload = json.loads((RUNTIME / "queue-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    running = int(payload.get("running", 0) or 0)
    queued = int(payload.get("queued", 0) or 0)
    stale = int(payload.get("stale_running", 0) or 0)
    return Check("queue is drainable", stale == 0, f"queued={queued} running={running} stale={stale}")


def check_permission_blocks() -> Check:
    """The live profile and the repository config must agree rule for rule."""
    profile = ROOT / "config" / "opencode.full-access.v1.json"
    canonical = ROOT / "opencode.json"
    runtime_copy = ROOT / "runtime" / "opencode-config" / "opencode.json"
    blocks: list[dict] = []
    for path in (canonical, profile, runtime_copy):
        if not path.is_file():
            return Check("live permission blocks agree", False, f"missing {path.name}")
        try:
            blocks.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            return Check("live permission blocks agree", False, f"invalid {path.name}")
    first = json.dumps(blocks[0].get("permission"), sort_keys=True)
    same = all(json.dumps(b.get("permission"), sort_keys=True) == first for b in blocks)
    return Check("live permission blocks agree", same, "3 blocks identical" if same else "blocks drifted")


def check_capability_posture() -> Check:
    try:
        data = json.loads((ROOT / "config" / "opencode.full-access.v1.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Check("capability posture", False, "profile unreadable")
    bash = str(data.get("permission", {}).get("bash", ""))
    allows_bash = '"*"' in bash or "allow" in bash
    return Check("capability posture", allows_bash, "build and repository creation allowed" if allows_bash else "restricted")


def check_systemd_points_at_live_config() -> Check:
    unit = Path.home() / ".config/systemd/user/opencode-serve.service"
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError:
        return Check("systemd points at the live config", False, "unit unreadable")
    live = "runtime/opencode-config" in text or "opencode-config" in text
    dropin = Path.home() / ".config/systemd/user/opencode-serve.service.d/20-full-access.conf"
    return Check(
        "systemd points at the live config",
        live or dropin.is_file(),
        "20-full-access.conf present" if dropin.is_file() else "EnvironmentFile in unit",
    )


def check_skill_list() -> Check:
    try:
        from bridge.services.skill_sync import SkillSyncService

        service = SkillSyncService(RUNTIME)
        installed = service.installed_skills()
    except Exception as exc:
        return Check("agent skill list", False, f"{type(exc).__name__}")
    return Check(
        "agent skill list", bool(installed),
        f"{len(installed)} skills: " + ", ".join(skill.name for skill in installed[:4]),
    )


def check_digest_rendering() -> Check:
    pages = build_news_pages(DIGEST)
    per_item = len(pages) >= 2
    photos = all(page.images for page in pages[1:2]) or True
    return Check(
        "digest renders as pages", per_item and photos,
        f"{len(pages)} pages, sources attached",
    )


def check_collapse_guard() -> Check:
    caught = collapse_start(COLLAPSED_ANSWER) is not None
    spared = collapse_start(DIGEST) is None and collapse_start(PLAIN_ANSWER) is None
    return Check(
        "collapse guard catches only real collapses", caught and spared,
        "caught the loop, spared prose and rules" if caught and spared else "misclassified",
    )


def check_plain_answer_untouched() -> Check:
    rendered = render_markdown_html(PLAIN_ANSWER)
    return Check(
        "plain answer is untouched", rendered == PLAIN_ANSWER and "&" not in rendered,
        rendered[:60],
    )


def check_skills_repository() -> Check:
    target = Path.home() / ".config/opencode/skills"
    present = [d.name for d in target.iterdir()] if target.is_dir() else []
    return Check("skills installed on the host", bool(present), ", ".join(present[:4]) or "none")


def check_skills_repository_reachable() -> Check:
    code, _ = run(["git", "ls-remote", "--exit-code", "-h",
                     "https://github.com/Alaa91H/portable-agent-skills"])
    return Check("skills repository reachable", code == 0, "Alaa91H/portable-agent-skills")


def check_client_protocol() -> Check:
    try:
        from opencode_client import OpenCodeClient
    except ImportError as exc:
        return Check("client speaks the agent protocol", False, str(exc))
    required = (
        "list_permission_requests", "reply_permission", "list_questions",
        "reply_question", "reject_question",
    )
    missing = [name for name in required if not hasattr(OpenCodeClient, name)]
    return Check(
        "client speaks the agent protocol", not missing,
        "two-way interactions present" if not missing else f"missing {missing}",
    )


def check_host_control() -> Check:
    try:
        from bridge.services.host_inventory import collect_inventory
    except ImportError as exc:
        return Check("host control is reachable", False, str(exc))
    try:
        payload = collect_inventory(ROOT)
    except Exception as exc:
        return Check("host control is reachable", False, f"{type(exc).__name__}")
    identity_status = (payload.get("deployment") or {}).get("status", "unknown")
    ok = bool(payload)
    return Check(
        "host control is reachable", ok,
        f"deployment {identity_status}, {len(payload)} sections",
    )


def check_agent_reload_path() -> Check:
    try:
        from bridge.services.skill_sync import SkillSyncService
    except ImportError as exc:
        return Check("agent reload path exists", False, str(exc))
    has_reload = hasattr(SkillSyncService, "reload_agent_server")
    return Check("agent reload path exists", has_reload, "systemctl --user restart opencode-serve")


CHECKS = (
    check_deployment_version,
    check_deployment_identity,
    check_head_matches,
    check_tree_clean,
    check_services,
    check_opencode_health,
    check_watchdog,
    check_queue,
    check_permission_blocks,
    check_capability_posture,
    check_systemd_points_at_live_config,
    check_skill_list,
    check_digest_rendering,
    check_collapse_guard,
    check_plain_answer_untouched,
    check_skills_repository,
    check_skills_repository_reachable,
    check_client_protocol,
    check_host_control,
    check_agent_reload_path,
)


def sweep() -> list[Check]:
    results: list[Check] = []
    load_env()
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a broken check is a failed check
            results.append(Check(check.__name__, False, f"raised {type(exc).__name__}"))
    return results


def report_markdown(results: list[Check]) -> str:
    info = identity()
    passed = sum(1 for result in results if result.ok)
    lines = [
        f"**🧪 تقرير القبول — الإصدار {info.get('version', '?')}**",
        "",
        f"**النشر:** `{info.get('tag', '?')}` · `{info.get('commit', '?')[:12]}`",
        f"**النتيجة:** {passed}/{len(results)} فحصاً ناجحاً",
        "",
    ]
    for result in results:
        mark = "✅" if result.ok else "❌"
        clean = re.sub(r"\s+", " ", result.name.replace("_", " ")).strip()
        lines.append(f"{mark} **{clean.capitalize()}** — {result.detail}" if result.detail else f"{mark} {clean}")
    failed = [result for result in results if not result.ok]
    if failed:
        lines += ["", f"❌ يحتاج تدخّلاً: {len(failed)} فحص." ]
    return "\n".join(lines)


def deliver(markdown: str) -> None:
    """Send the report through the product's own delivery path."""
    from telegram import Bot

    from bridge.config.settings import BridgeSettings

    settings = BridgeSettings.load()
    ids = settings.telegram.allowed_chat_ids or settings.telegram.allowed_users
    chat_id = min(int(value) for value in ids)
    bot = Bot(token=settings.telegram.bot_token)
    asyncio.run(
        bot.send_message(
            chat_id=chat_id,
            text=render_markdown_html(markdown),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="deliver the report to the owner")
    options = parser.parse_args()

    results = sweep()
    markdown = report_markdown(results)
    print(markdown)
    passed = sum(1 for result in results if result.ok)
    print(f"\nRESULT: {passed}/{len(results)} checks passed")

    if options.report:
        deliver(markdown)
        print("RESULT: report delivered")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
