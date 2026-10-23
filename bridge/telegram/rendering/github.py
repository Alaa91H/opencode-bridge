"""Plain-text Telegram cards for GitHub administration."""

from __future__ import annotations

from typing import Any

MAX_LIST = 20


def _visibility(value: str) -> str:
    return "خاص" if value.upper() in {"PRIVATE", "INTERNAL"} else "عام"


def _labels(items: tuple[str, ...]) -> str:
    return f" [{', '.join(items[:6])}]" if items else ""


def render_help() -> str:
    return (
        "إدارة GitHub من المحادثة — بدون بناء على السيرفر، التحليل والتعديل والدفع فقط.\n\n"
        "الحساب والمستودعات:\n"
        "/gh status — الحساب المسجّل والمستودعات المُدارة\n"
        "/gh repos [limit] — مستودعات الحساب\n"
        "/gh repo [owner/repo] — بطاقة مستودع (الافتراضي النشط)\n"
        "/gh add owner/repo — استلام إدارة مستودع\n"
        "/gh remove owner/repo — إزالة من قائمة الإدارة (لا يحذف المستودع)\n"
        "/gh managed — المستودعات المُدارة\n"
        "/gh create name [private|public] [| وصف] — إنشاء مستودع\n"
        "/gh use owner/repo — تعيين المستودع النشط\n"
        "/gh sync — مزامنة المستودع النشط\n\n"
        "التطوير والمراجعة:\n"
        "/gh push وصف التغيير — commit + push لكل تغييرات المستودع النشط\n"
        "/gh prs [slug] [open|closed|all] — طلبات الدمج\n"
        "/gh pr [slug] N — تفاصيل طلب دمج\n"
        "/gh prnew العنوان | الفرع | الهدف [| الوصف] — إنشاء PR\n"
        "/gh merge N [squash|merge|rebase] — دمج PR\n"
        "/gh issues [slug] [open|closed|all] — المشاكل\n"
        "/gh issue العنوان | الوصف — إنشاء issue\n"
        "/gh comment N نص — تعليق على issue أو PR\n"
        "/gh branches [slug] — الفروع\n"
        "/gh releases [slug] — الإصدارات\n"
        "/gh relnew tag | العنوان [| الملاحظات] — إنشاء إصدار\n\n"
        "التحقق النهائي يبقى على GitHub Actions (/ci)؛ لا يشغّل الجسر بناءات محلية."
    )


def render_status(login: str, active_slug: str, managed: tuple[str, ...]) -> str:
    repos = "\n".join(f"- {slug}" for slug in managed[:MAX_LIST]) or "- لا يوجد"
    active = active_slug or "غير محدد (/gh use owner/repo)"
    return (
        f"حساب GitHub: {login}\n"
        f"المستودع النشط: {active}\n"
        f"المستودعات المُدارة ({len(managed)}):\n{repos}"
    )


def render_repo(repo: Any, *, managed: bool, active: bool) -> str:
    flags = []
    if managed:
        flags.append("مُدار")
    if active:
        flags.append("نشط")
    suffix = f"\nالحالة: {'، '.join(flags)}" if flags else ""
    description = f"\nالوصف: {repo.description}" if repo.description else ""
    pushed = f"\nآخر دفعة: {repo.pushed_at}" if repo.pushed_at else ""
    return (
        f"{repo.slug}\n"
        f"الظهور: {_visibility(repo.visibility)}\n"
        f"الفرع الافتراضي: {repo.default_branch or '—'}"
        f"{description}{pushed}{suffix}\n{repo.url}"
    )


def render_repos(items: tuple[Any, ...], managed: tuple[str, ...]) -> str:
    if not items:
        return "لا توجد مستودعات ظاهرة لهذا الحساب."
    managed_keys = {slug.casefold() for slug in managed}
    lines = [f"مستودعات الحساب ({min(len(items), MAX_LIST)}):"]
    for repo in items[:MAX_LIST]:
        mark = " [مُدار]" if repo.slug.casefold() in managed_keys else ""
        lines.append(
            f"- {repo.slug}{mark} — {_visibility(repo.visibility)}"
            + (f" — {repo.description[:80]}" if repo.description else "")
        )
    return "\n".join(lines)


def render_pulls(slug: str, items: tuple[Any, ...]) -> str:
    if not items:
        return f"{slug}: لا توجد طلبات دمج بهذه الحالة."
    lines = [f"طلبات الدمج — {slug}:"]
    for pull in items[:MAX_LIST]:
        draft = " [مسودة]" if pull.draft else ""
        lines.append(
            f"#{pull.number}{draft} {pull.title[:90]}\n"
            f"  {pull.head} → {pull.base} · {pull.state} · @{pull.author or '—'}"
        )
    return "\n".join(lines)


def render_pull(detail: Any) -> str:
    pull = detail.summary
    body = " ".join(detail.body.split())
    if len(body) > 600:
        body = body[:600].rstrip() + "…"
    lines = [
        f"#{pull.number} {pull.title}",
        f"الحالة: {pull.state}{' · مسودة' if pull.draft else ''}",
        f"الفروع: {pull.head} → {pull.base}",
        f"المؤلف: @{pull.author or '—'}",
        f"التغييرات: +{detail.additions} −{detail.deletions} في {detail.changed_files} ملف",
    ]
    if detail.mergeable:
        lines.append(f"قابلية الدمج: {detail.mergeable}")
    if body:
        lines.append(f"\n{body}")
    if pull.url:
        lines.append(pull.url)
    return "\n".join(lines)


def render_issues(slug: str, items: tuple[Any, ...]) -> str:
    if not items:
        return f"{slug}: لا توجد مشاكل بهذه الحالة."
    lines = [f"المشاكل — {slug}:"]
    for issue in items[:MAX_LIST]:
        lines.append(
            f"#{issue.number} {issue.title[:90]}{_labels(issue.labels)}"
            f" · {issue.state} · @{issue.author or '—'}"
        )
    return "\n".join(lines)


def render_branches(slug: str, items: tuple[str, ...]) -> str:
    if not items:
        return f"{slug}: لا توجد فروع ظاهرة."
    return f"فروع {slug} ({len(items)}):\n" + "\n".join(
        f"- {name}" for name in items[:MAX_LIST]
    )


def render_releases(slug: str, items: tuple[Any, ...]) -> str:
    if not items:
        return f"{slug}: لا توجد إصدارات."
    lines = [f"إصدارات {slug}:"]
    for release in items[:MAX_LIST]:
        flags = []
        if release.draft:
            flags.append("مسودة")
        if release.prerelease:
            flags.append("تجريبي")
        suffix = f" [{'، '.join(flags)}]" if flags else ""
        lines.append(
            f"- {release.tag}{suffix} — {release.name or release.tag}"
            + (f" · {release.published_at}" if release.published_at else "")
        )
    return "\n".join(lines)


def render_push(slug: str, result: Any) -> str:
    if not result.changed:
        return f"{slug}: {result.message}"
    return (
        f"{slug}: {result.message}\n"
        f"الفرع: {result.branch}\n"
        f"الالتزام: {result.commit[:12]}"
    )
