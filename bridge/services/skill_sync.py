"""Keep the agent's skills in step with their repository, automatically.

The agent discovers skills from ``~/.config/opencode/skills/<name>/SKILL.md``,
and it reads that directory when the agent server starts. Two consequences shape
this service. First, writing files is not enough: the server has to be restarted
before the agent can see a new skill, so a sync that changed anything ends by
asking for a reload. Second, a skills repository lands inside the agent's own
instruction path, where a malicious or broken file becomes behaviour, so every
skill is validated against the published Agent Skills rules before it is
published, and the batch is published all-or-nothing.

The bridge owns only what it published. A skill the owner created by hand, or a
built-in skill, is never read, rewritten or deleted by a sync.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from bridge.services.host_control import CommandResult, HostControlError, run_fixed

log = logging.getLogger("opencode_bridge.skills")

DEFAULT_SOURCE = "https://github.com/Alaa91H/portable-agent-skills"
DEFAULT_TARGET = Path.home() / ".config" / "opencode" / "skills"
STATE_FILENAME = "skills-state.json"

# The published rules, enforced here rather than trusted from the repository.
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
# A portable collection now carries 84 skills and grows; the cap is there to
# stop a mistaken or hostile repository flooding the agent's skill directory,
# not to describe the collection. 200 leaves room for roughly twice the current
# set while still refusing anything absurd.
MAX_SKILLS = 200
MAX_SKILL_FILE_BYTES = 512 * 1024
MAX_SKILL_DIR_BYTES = 4 * 1024 * 1024
MAX_BATCH_BYTES = 32 * 1024 * 1024

_FRONTMATTER_KEYS = ("name", "description", "license", "compatibility")


class SkillSyncError(RuntimeError):
    """A sync could not be completed; the installed skills are untouched."""


@dataclass(frozen=True)
class Skill:
    """One validated skill, ready to publish."""

    name: str
    description: str
    source_dir: Path
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class SyncResult:
    status: str
    commit: str = ""
    installed: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    message: str = ""
    needs_reload: bool = False
    # What the agent will have on disk after this sync, keyed by name and digest.
    managed: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"synced", "unchanged", "empty"}


@dataclass
class _State:
    source: str = DEFAULT_SOURCE
    commit: str = ""
    synced_at: float = 0.0
    managed: dict[str, str] = field(default_factory=dict)
    needs_reload: bool = False
    last_error: str = ""
    last_status: str = ""


def _frontmatter(text: str) -> dict[str, str]:
    """Read the leading ``---`` block. Only flat ``key: value`` pairs matter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return fields
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        fields[key.strip().lower()] = value.strip().strip('"').strip("'")
    return {}


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise SkillSyncError("a skill must not contain a symlink")
        if item.is_file():
            total += item.stat().st_size
    return total


def validate_skill(directory: Path) -> Skill:
    """Check one candidate directory against the Agent Skills rules."""
    name = directory.name
    if not NAME_RE.match(name) or len(name) > MAX_NAME:
        raise SkillSyncError(f"invalid skill name: {name}")
    manifest = directory / "SKILL.md"
    if not manifest.is_file() or manifest.is_symlink():
        raise SkillSyncError(f"{name}: SKILL.md is missing")
    size = manifest.stat().st_size
    if size > MAX_SKILL_FILE_BYTES:
        raise SkillSyncError(f"{name}: SKILL.md is too large")
    text = manifest.read_text(encoding="utf-8", errors="replace")
    fields = _frontmatter(text)
    if fields.get("name") != name:
        raise SkillSyncError(f"{name}: frontmatter name does not match the folder")
    description = fields.get("description", "")
    if not 1 <= len(description) <= MAX_DESCRIPTION:
        raise SkillSyncError(f"{name}: description must be 1-{MAX_DESCRIPTION} characters")
    total = _directory_size(directory)
    if total > MAX_SKILL_DIR_BYTES:
        raise SkillSyncError(f"{name}: skill directory is too large")
    return Skill(
        name=name,
        description=description,
        source_dir=directory,
        digest=_digest(directory),
        size_bytes=total,
    )


def _digest(directory: Path) -> str:
    """A content fingerprint of the whole skill, so a no-op sync stays a no-op."""
    parts = [
        f"{item.relative_to(directory).as_posix()}:{hashlib.sha256(item.read_bytes()).hexdigest()}"
        for item in sorted(directory.rglob("*"))
        if item.is_file()
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def skill_roots(root: Path) -> tuple[Path, ...]:
    """Where skills live in a checkout, in the order they should be trusted.

    A collection that serves many agents keeps one canonical copy in ``skills/``
    and mirrors it for each tool it supports — ``.agents/``, ``.claude/``,
    ``.cursor/``, ``.windsurf/`` and the rest. Scanning the whole checkout would
    find the same 84 skills eight times, and scanning only the root finds none of
    them, because the root holds ``skills/``, ``docs/``, ``spec/`` and ``tests/``
    rather than skill folders.

    So exactly one location is chosen: ``skills/`` when it exists, otherwise the
    checkout root, which keeps older single-skill repositories working.
    """
    nested = root / "skills"
    if nested.is_dir() and not nested.is_symlink():
        return (nested,)
    return (root,)


def collect_skills(root: Path) -> tuple[tuple[Skill, ...], tuple[str, ...]]:
    """Validate every skill folder in a checkout, reporting the ones refused."""
    accepted: list[Skill] = []
    rejected: list[str] = []
    if not root.is_dir():
        return (), ()
    for base in skill_roots(root):
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            if directory.name.startswith("."):
                continue
            try:
                accepted.append(validate_skill(directory))
            except SkillSyncError as exc:
                rejected.append(f"{directory.name}: {exc}")
            except OSError as exc:
                rejected.append(f"{directory.name}: {type(exc).__name__}")
    if len(accepted) > MAX_SKILLS:
        raise SkillSyncError(f"too many skills in one sync ({len(accepted)})")
    total = sum(skill.size_bytes for skill in accepted)
    if total > MAX_BATCH_BYTES:
        raise SkillSyncError("skills batch is too large")
    return tuple(accepted), tuple(rejected)


class SkillSyncService:
    """Pull, validate and publish the agent's skills, and say what changed."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        target_dir: Path | None = None,
        source: str = DEFAULT_SOURCE,
        git: str = "git",
        runner: object = run_fixed,
        logger: logging.Logger | None = None,
    ) -> None:
        self.directory = Path(runtime_dir)
        self.target = Path(target_dir or DEFAULT_TARGET)
        self.source = source
        self.git = git
        self.runner = runner
        self.log = logger or log
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ state

    @property
    def state_path(self) -> Path:
        return self.directory / STATE_FILENAME

    def _read_state(self) -> _State:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _State(source=self.source)
        if not isinstance(raw, dict):
            return _State(source=self.source)
        managed = raw.get("managed")
        return _State(
            source=str(raw.get("source") or self.source),
            commit=str(raw.get("commit") or ""),
            synced_at=float(raw.get("synced_at") or 0.0),
            managed={str(k): str(v) for k, v in managed.items()} if isinstance(managed, dict) else {},
            needs_reload=bool(raw.get("needs_reload")),
            last_error=str(raw.get("last_error") or ""),
            last_status=str(raw.get("last_status") or ""),
        )

    def _write_state(self, state: _State) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "source": state.source,
            "commit": state.commit,
            "synced_at": state.synced_at,
            "managed": state.managed,
            "needs_reload": state.needs_reload,
            "last_error": state.last_error,
            "last_status": state.last_status,
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)

    # ---------------------------------------------------------------- publish

    def _prepare_target(self) -> None:
        if self.target.is_symlink():
            raise SkillSyncError("skills directory must not be a symlink")
        self.target.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.target.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise SkillSyncError("skills path is not a directory")
        if os.name == "posix" and metadata.st_uid != os.geteuid():
            raise SkillSyncError("skills directory belongs to another user")

    def _publish(self, skill: Skill, stamp: str) -> None:
        incoming = self.target / f".incoming-{skill.name}-{stamp}"
        retired = self.target / f".retired-{skill.name}-{stamp}"
        shutil.rmtree(incoming, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
        shutil.copytree(skill.source_dir, incoming, symlinks=False)
        live = self.target / skill.name
        moved_away = False
        try:
            if live.exists():
                os.replace(live, retired)
                moved_away = True
            os.replace(incoming, live)
        except OSError as exc:
            if moved_away:
                os.replace(retired, live)
            shutil.rmtree(incoming, ignore_errors=True)
            raise SkillSyncError(f"{skill.name}: could not publish ({type(exc).__name__})") from exc
        shutil.rmtree(retired, ignore_errors=True)

    def _remove(self, name: str) -> None:
        live = self.target / name
        if not live.is_dir() or live.is_symlink():
            return
        shutil.rmtree(live, ignore_errors=True)

    # ------------------------------------------------------------------- sync

    async def _run(self, argv: list[str], *, timeout: float) -> CommandResult:
        """Run one fixed command through the host-control helper, never a shell."""
        try:
            return await self.runner(argv, timeout=timeout)  # type: ignore[operator]
        except HostControlError as exc:
            raise SkillSyncError(str(exc)) from exc

    async def sync(self, *, timeout: float = 180.0) -> SyncResult:
        """Fetch, validate and publish. The installed set survives any failure."""
        async with self._lock:
            state = self._read_state()
            try:
                result = await self._sync_once(state, timeout)
            except SkillSyncError as exc:
                state.last_error = str(exc)
                state.last_status = "failed"
                self._write_state(state)
                return SyncResult(status="failed", message=str(exc), commit=state.commit)
            except (OSError, TimeoutError) as exc:
                state.last_error = f"{type(exc).__name__}"
                state.last_status = "failed"
                self._write_state(state)
                return SyncResult(status="failed", message=type(exc).__name__, commit=state.commit)
            state.commit = result.commit
            state.synced_at = time.time()
            state.last_error = ""
            state.last_status = result.status
            state.needs_reload = result.needs_reload or state.needs_reload
            state.source = self.source
            if result.managed:
                # Only a sync that actually published replaces the ledger; an
                # empty repository or a failure must not erase what is running.
                state.managed = dict(result.managed)
            self._write_state(state)
            return result

    async def _sync_once(self, state: _State, timeout: float) -> SyncResult:
        stamp = f"{int(time.time())}"
        staging = self.directory / f"skills-staging-{stamp}"
        shutil.rmtree(staging, ignore_errors=True)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            result = await self._run(
                [self.git, "clone", "--depth", "1", "--quiet", self.source, str(staging)],
                timeout=timeout,
            )
            if result.code != 0:
                raise SkillSyncError(_clean(result.output) or "could not reach the skills repository")
            head = await self._run(
                [self.git, "-C", str(staging), "rev-parse", "HEAD"], timeout=30.0,
            )
            commit = _clean(head.output) if head.code == 0 else ""
            skills, rejected = collect_skills(staging)
            if not skills:
                # An empty repository is a real state, not a reason to remove
                # every skill the agent has been working with.
                self._prepare_target()
                return SyncResult(
                    status="empty",
                    commit=commit,
                    rejected=rejected,
                    message="skills repository has no skills yet",
                    needs_reload=False,
                )
            self._prepare_target()
            known = dict(state.managed)
            installed: list[str] = []
            updated: list[str] = []
            for skill in skills:
                previous = known.get(skill.name)
                if previous != skill.digest:
                    self._publish(skill, stamp)
                if previous is None:
                    installed.append(skill.name)
                elif previous != skill.digest:
                    updated.append(skill.name)
            removed = tuple(sorted(name for name in known if name not in {s.name for s in skills}))
            for name in removed:
                self._remove(name)
            managed = {skill.name: skill.digest for skill in skills}
            changed = bool(installed or updated or removed)
            return SyncResult(
                status="synced" if changed else "unchanged",
                commit=commit,
                installed=tuple(sorted(installed)),
                updated=tuple(sorted(updated)),
                removed=removed,
                rejected=rejected,
                message=f"{len(skills)} skills",
                needs_reload=changed or state.needs_reload,
                managed=managed,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # ----------------------------------------------------------------- status

    def installed_skills(self) -> tuple[Skill, ...]:
        """What the agent can actually load right now, by reading its own folder."""
        if not self.target.is_dir():
            return ()
        found: list[Skill] = []
        for directory in sorted(self.target.iterdir()):
            if directory.name.startswith(".") or not directory.is_dir():
                continue
            try:
                found.append(validate_skill(directory))
            except SkillSyncError:
                continue
        return tuple(found)

    def status(self) -> dict[str, object]:
        state = self._read_state()
        skills = self.installed_skills()
        return {
            "source": self.source,
            "commit": state.commit,
            "synced_at": state.synced_at,
            "last_status": state.last_status,
            "last_error": state.last_error,
            "needs_reload": state.needs_reload,
            "managed_count": len(state.managed),
            "installed": skills,
        }

    def clear_reload(self) -> None:
        state = self._read_state()
        if state.needs_reload:
            state.needs_reload = False
            self._write_state(state)

    async def reload_agent_server(self, *, timeout: float = 120.0) -> tuple[bool, str]:
        """Restart the agent server so it re-reads the skills directory."""
        try:
            outcome = await self._run(
                ["systemctl", "--user", "restart", "opencode-serve.service"], timeout=timeout,
            )
        except SkillSyncError as exc:
            return False, str(exc)
        if outcome.code != 0:
            return False, _clean(outcome.output) or "restart failed"
        self.clear_reload()
        return True, "ok"


def _clean(text: str) -> str:
    cleaned = "".join(ch for ch in str(text or "") if ch == "\n" or ord(ch) >= 32)
    return cleaned.strip().splitlines()[0][:200] if cleaned.strip() else ""
