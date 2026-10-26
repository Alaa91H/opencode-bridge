"""Owner-controlled management of the optional host tools the agent uses.

The agent can only use what the host already provides.  This service gives the
owner one place to see those tools, switch a capability off without touching the
package, and install, upgrade or remove an allow-listed package when it is
actually needed.

Safety boundaries
-----------------
* The catalogue is a fixed map from a short key to one Debian package.  No
  package name, path or shell text ever comes from a callback or a command.
* Package changes run through a small root-owned helper with its own copy of
  the same map.  The bridge user cannot widen that map, and the helper refuses
  to remove the core tools.
* Destructive package work participates in the process-wide host-operation
  guard, so it cannot overlap an operating-system upgrade or a reboot.
* Disabling a tool is a policy decision, not a package change.  The bridge
  features that depend on that capability stop using it immediately, while the
  package stays available for a later one-tap re-enable.
* Removal is real and is recorded in the policy so the daily provisioning run
  does not silently reinstall a package the owner deliberately removed.
"""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from bridge.services.host_control import CommandResult, redact, run_fixed
from bridge.services.host_credentials import secure_write
from bridge.services.host_operations import HOST_OPERATIONS

POLICY_SCHEMA = 1
POLICY_FILENAME = "tool-policy.json"
HELPER_PATH = Path("/usr/local/sbin/opencode-bridge-tools")
DEFAULT_TIMEOUTS = {
    "install": 900.0,
    "update": 900.0,
    "remove": 300.0,
    "verify": 20.0,
}


class ToolManagerError(ValueError):
    """A caller asked for an unknown tool or an unsafe transition."""


@dataclass(frozen=True)
class ToolSpec:
    """One allow-listed package and the commands that prove it is usable."""

    key: str
    package: str
    commands: tuple[str, ...]
    category: str
    purpose: str
    required: bool = False
    recommended: bool = True
    heavy: bool = False

    @property
    def removable(self) -> bool:
        return not self.required


# The catalogue is deliberately small: every entry is a tool the Bridge or the
# agent has an actual use for.  A new package requires a code change and a root
# asset refresh, not an owner-supplied package name.
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("git", "git", ("git",), "source", "Git repositories and releases", required=True),
    ToolSpec("gh", "gh", ("gh",), "source", "GitHub administration and CI observation", required=True),
    ToolSpec("curl", "curl", ("curl",), "network", "HTTP transfers and API calls", required=True),
    ToolSpec("jq", "jq", ("jq",), "data", "JSON inspection and transformation"),
    ToolSpec("rg", "ripgrep", ("rg",), "search", "Fast repository search"),
    ToolSpec("yt-dlp", "yt-dlp", ("yt-dlp",), "media", "Video and audio downloads"),
    ToolSpec("aria2", "aria2", ("aria2c",), "downloads", "Parallel multi-connection downloads"),
    ToolSpec(
        "ffmpeg", "ffmpeg", ("ffmpeg", "ffprobe"), "media",
        "Audio and video inspection, extraction and conversion", heavy=True,
    ),
    ToolSpec("age", "age", ("age",), "backup", "Encrypted database backups"),
    ToolSpec(
        "poppler", "poppler-utils", ("pdftotext", "pdfimages"), "documents",
        "PDF text and image extraction",
    ),
    ToolSpec(
        "tesseract", "tesseract-ocr", ("tesseract",), "documents",
        "Optical character recognition for images and scanned PDFs", heavy=True,
    ),
)

SPECS = {tool.key: tool for tool in TOOLS}


@dataclass(frozen=True)
class ToolStatus:
    spec: ToolSpec
    installed: bool
    package_installed: bool
    enabled: bool
    version: str
    paths: tuple[str, ...]
    missing_commands: tuple[str, ...]
    unsafe_paths: tuple[str, ...]
    size_kib: int | None

    @property
    def key(self) -> str:
        return self.spec.key

    @property
    def usable(self) -> bool:
        return self.installed and self.enabled


@dataclass(frozen=True)
class ToolPolicy:
    disabled: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ToolActionResult:
    ok: bool
    code: str
    detail: str = ""

    @property
    def key(self) -> str:
        return self.code


@dataclass(frozen=True)
class ToolAudit:
    ok: bool
    checked: int
    issues: tuple[str, ...]


@dataclass(frozen=True)
class ToolActivation:
    ok: bool
    installed: tuple[str, ...]
    enabled: tuple[str, ...]
    failures: tuple[str, ...]
    audit: ToolAudit
    detail: str = ""


Progress = Callable[[str], Awaitable[None]]


def _spec(key: str) -> ToolSpec:
    try:
        return SPECS[key]
    except KeyError as exc:
        raise ToolManagerError("unknown tool") from exc


def _unsafe_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Report binaries that are missing, non-regular or writable by non-owners."""
    problems: list[str] = []
    for text in paths:
        path = Path(text)
        try:
            resolved = path.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            problems.append(f"{path.name}: missing")
            continue
        if not stat.S_ISREG(info.st_mode):
            problems.append(f"{path.name}: not a regular file")
            continue
        if info.st_mode & 0o022:
            problems.append(f"{path.name}: writable by group or others")
    return tuple(problems)


class ToolManager:
    """Inventory, capability policy and fixed package actions for one host."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        helper: Path | str = HELPER_PATH,
        runner: Callable[..., Awaitable[CommandResult]] = run_fixed,
        dpkg_query: str | None = None,
        dpkg_verify: str | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.helper = Path(helper)
        self.runner = runner
        self.dpkg_query = dpkg_query or shutil.which("dpkg-query") or "dpkg-query"
        self.dpkg_verify = dpkg_verify or shutil.which("dpkg") or "dpkg"

    # --------------------------------------------------------------- policy
    @property
    def policy_path(self) -> Path:
        return self.runtime_dir / POLICY_FILENAME

    def _read_policy(self) -> ToolPolicy:
        path = self.policy_path
        try:
            present = path.exists()
        except OSError as exc:
            # Python 3.12 and 3.13 re-raise unexpected errors from exists()
            # while 3.14 swallows them via os.path.exists. An unreadable policy
            # must fail closed the same way on every interpreter instead of
            # looking like an absent policy that enables every capability.
            raise ToolManagerError("cannot read tool policy") from exc
        if not present:
            return ToolPolicy()
        try:
            info = path.lstat()
        except OSError as exc:
            raise ToolManagerError("cannot read tool policy") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ToolManagerError("tool policy must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise ToolManagerError("tool policy must be a regular file")
        if info.st_size > 64 * 1024:
            raise ToolManagerError("tool policy is too large")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolManagerError("tool policy is not valid JSON") from exc
        if (
            not isinstance(raw, dict)
            or type(raw.get("schema")) is not int
            or raw["schema"] != POLICY_SCHEMA
        ):
            raise ToolManagerError("unsupported tool policy schema")
        disabled = raw.get("disabled", [])
        removed = raw.get("removed", [])
        if not isinstance(disabled, list) or not isinstance(removed, list):
            raise ToolManagerError("tool policy lists are invalid")
        known = set(SPECS)
        return ToolPolicy(
            disabled=frozenset(str(item) for item in disabled if item in known),
            removed=frozenset(str(item) for item in removed if item in known),
        )

    def _write_policy(self, *, disabled: Iterable[str], removed: Iterable[str]) -> None:
        known = set(SPECS)
        payload = {
            "schema": POLICY_SCHEMA,
            "disabled": sorted({item for item in disabled if item in known}),
            "removed": sorted({item for item in removed if item in known}),
        }
        secure_write(
            self.policy_path,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        )

    def enabled(self, key: str) -> bool:
        """Whether a capability is allowed; unknown keys are never enabled."""
        _spec(key)
        try:
            return key not in self._read_policy().disabled
        except ToolManagerError:
            # A corrupt policy must not silently turn a capability back on.
            return False

    def disabled_tools(self) -> frozenset[str]:
        try:
            return self._read_policy().disabled
        except ToolManagerError:
            return frozenset(SPECS)

    def set_enabled(self, key: str, enabled: bool) -> ToolStatus:
        spec = _spec(key)
        if not enabled and spec.required:
            raise ToolManagerError("core tools cannot be disabled")
        policy = self._read_policy()
        disabled = set(policy.disabled)
        if enabled:
            disabled.discard(key)
        else:
            disabled.add(key)
        self._write_policy(disabled=disabled, removed=policy.removed)
        return self._status_one_sync(spec, enabled=enabled and key not in policy.removed)

    # ------------------------------------------------------------ inventory
    async def _package_info(self, spec: ToolSpec) -> tuple[bool, str, int | None]:
        try:
            result = await self.runner(
                [self.dpkg_query, "-W", "-f=${Status}\t${Version}\t${Installed-Size}\n", spec.package],
                timeout=8,
            )
        except Exception:
            return False, "", None
        line = result.output.strip().splitlines()[0] if result.output.strip() else ""
        fields = line.split("\t")
        status = fields[0].strip() if fields else ""
        version = fields[1].strip() if len(fields) > 1 else ""
        size: int | None = None
        if len(fields) > 2:
            try:
                size = int(fields[2].strip()) or None
            except ValueError:
                size = None
        return status == "install ok installed", version, size

    def _status_one_sync(self, spec: ToolSpec, *, enabled: bool) -> ToolStatus:
        found = tuple(shutil.which(command) for command in spec.commands)
        paths = tuple(item for item in found if item)
        missing = tuple(
            command for command, path in zip(spec.commands, found, strict=True) if not path
        )
        return ToolStatus(
            spec=spec,
            installed=not missing,
            package_installed=False,
            enabled=enabled,
            version="",
            paths=paths,
            missing_commands=missing,
            unsafe_paths=_unsafe_paths(paths),
            size_kib=None,
        )

    async def _status_one(self, spec: ToolSpec, *, policy: ToolPolicy) -> ToolStatus:
        base = self._status_one_sync(spec, enabled=spec.key not in policy.disabled)
        package_installed, version, size = await self._package_info(spec)
        return ToolStatus(
            **{
                **base.__dict__,
                "package_installed": package_installed,
                "version": version,
                "size_kib": size,
            }
        )

    async def status(self) -> tuple[ToolStatus, ...]:
        policy = self._read_policy()
        return tuple([await self._status_one(spec, policy=policy) for spec in TOOLS])

    async def status_for(self, key: str) -> ToolStatus:
        spec = _spec(key)
        return await self._status_one(spec, policy=self._read_policy())

    # ------------------------------------------------------------- actions
    @property
    def busy(self) -> bool:
        return HOST_OPERATIONS.busy

    def helper_available(self) -> bool:
        try:
            info = self.helper.stat()
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and not info.st_mode & 0o022

    async def _exclusive(self, operation: Callable[[], Awaitable[ToolActionResult]]) -> ToolActionResult:
        if HOST_OPERATIONS.busy:
            return ToolActionResult(False, "busy", HOST_OPERATIONS.current or "")
        result = await HOST_OPERATIONS.run("tools", operation)
        if isinstance(result, ToolActionResult):
            return result
        # A racing operation won the guard between the check and the run.
        return ToolActionResult(False, "busy", str(result))

    async def install(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        return await self._exclusive(lambda: self._install(key, progress=progress))

    async def update(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        return await self._exclusive(lambda: self._update(key, progress=progress))

    async def remove(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        return await self._exclusive(lambda: self._remove(key, progress=progress))

    async def activate_all(self, *, progress: Progress | None = None) -> ToolActivation:
        if HOST_OPERATIONS.busy:
            return ToolActivation(
                False, (), (), ("busy",), ToolAudit(False, 0, ("host operation in progress",)),
                HOST_OPERATIONS.current or "",
            )
        result = await HOST_OPERATIONS.run(
            "tools", lambda: self._activate_all(progress=progress)
        )
        if isinstance(result, ToolActivation):
            return result
        return ToolActivation(
            False, (), (), ("busy",), ToolAudit(False, 0, ("host operation in progress",)),
            str(result),
        )

    async def _notify(self, progress: Progress | None, message: str) -> None:
        if progress is None:
            return
        try:
            await progress(message)
        except Exception:
            pass

    async def _install(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        spec = _spec(key)
        policy = self._read_policy()
        before = await self.status_for(key)
        if before.installed:
            disabled = set(policy.disabled)
            disabled.discard(key)
            removed = set(policy.removed)
            removed.discard(key)
            self._write_policy(disabled=disabled, removed=removed)
            return ToolActionResult(True, "already_installed")
        if not self.helper_available():
            return ToolActionResult(False, "unavailable", "root tool helper is not installed")
        await self._notify(progress, f"installing {spec.package}")
        result = await self._run_helper("install", spec)
        if not result.ok:
            return result
        after = await self.status_for(key)
        if not after.installed:
            return ToolActionResult(
                False, "not_usable",
                "package operation finished but the command is not available",
            )
        disabled = set(policy.disabled)
        disabled.discard(key)
        removed = set(policy.removed)
        removed.discard(key)
        self._write_policy(disabled=disabled, removed=removed)
        return ToolActionResult(True, "installed")

    async def _update(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        spec = _spec(key)
        before = await self.status_for(key)
        if not before.installed:
            return ToolActionResult(False, "not_installed")
        if not before.package_installed:
            return ToolActionResult(False, "manual_install")
        if not self.helper_available():
            return ToolActionResult(False, "unavailable", "root tool helper is not installed")
        await self._notify(progress, f"updating {spec.package}")
        return await self._run_helper("update", spec)

    async def _remove(self, key: str, *, progress: Progress | None = None) -> ToolActionResult:
        spec = _spec(key)
        if spec.required:
            return ToolActionResult(False, "core_tool")
        policy = self._read_policy()
        before = await self.status_for(key)
        if not before.installed and not before.package_installed:
            return ToolActionResult(False, "not_installed")
        if not self.helper_available():
            return ToolActionResult(False, "unavailable", "root tool helper is not installed")
        await self._notify(progress, f"removing {spec.package}")
        result = await self._run_helper("remove", spec)
        if not result.ok:
            return result
        after = await self.status_for(key)
        if after.installed or after.package_installed:
            return ToolActionResult(False, "still_present")
        disabled = set(policy.disabled)
        disabled.add(key)
        removed = set(policy.removed)
        removed.add(key)
        self._write_policy(disabled=disabled, removed=removed)
        return ToolActionResult(True, "removed")

    async def _activate_all(self, *, progress: Progress | None = None) -> ToolActivation:
        installed: list[str] = []
        enabled: list[str] = []
        failures: list[str] = []
        for spec in TOOLS:
            await self._notify(progress, f"checking {spec.package}")
            current = await self.status_for(spec.key)
            if not current.installed:
                result = await self._install(spec.key, progress=progress)
                if not result.ok:
                    failures.append(f"{spec.key}:{result.code}")
                    continue
                installed.append(spec.key)
            else:
                if not current.enabled:
                    self.set_enabled(spec.key, True)
                    enabled.append(spec.key)
        audit = await self.harden()
        return ToolActivation(
            not failures and audit.ok,
            tuple(installed),
            tuple(enabled),
            tuple(failures),
            audit,
            "" if not failures else "some tools could not be installed",
        )

    async def _run_helper(self, action: str, spec: ToolSpec) -> ToolActionResult:
        timeout = DEFAULT_TIMEOUTS.get(action, 600.0)
        try:
            result = await self.runner(
                ["sudo", "-n", str(self.helper), action, spec.key], timeout=timeout,
            )
        except Exception as exc:
            return ToolActionResult(False, "failed", type(exc).__name__)
        if result.code == 0:
            code = {"install": "installed", "update": "updated", "remove": "removed"}[action]
            return ToolActionResult(True, code)
        detail = redact(result.output).strip()[-1200:]
        if result.code in {126, 127}:
            return ToolActionResult(False, "unavailable", detail or "root tool helper is not authorized")
        return ToolActionResult(False, "failed", detail or f"helper exited {result.code}")

    # --------------------------------------------------------------- audit
    async def harden(self) -> ToolAudit:
        try:
            policy = self._read_policy()
        except ToolManagerError as exc:
            return ToolAudit(False, 0, (str(exc),))
        issues: list[str] = []
        if self.policy_path.exists() and not self.policy_path.is_symlink():
            try:
                if self.policy_path.stat().st_mode & 0o077:
                    issues.append("tool policy is readable or writable by others")
            except OSError:
                issues.append("tool policy permissions could not be checked")
        if not self.helper_available():
            issues.append("root tool helper is missing or writable by others")
        statuses = [await self._status_one(spec, policy=policy) for spec in TOOLS]
        for status in statuses:
            spec = status.spec
            if spec.required and not status.installed:
                issues.append(f"{spec.key}: required command is missing")
            if status.package_installed and not status.installed:
                issues.append(f"{spec.key}: package is installed but its command is missing")
            for problem in status.unsafe_paths:
                issues.append(f"{spec.key}: {problem}")
            if status.package_installed:
                try:
                    verified = await self.runner(
                        [self.dpkg_verify, "--verify", spec.package], timeout=DEFAULT_TIMEOUTS["verify"],
                    )
                except Exception:
                    issues.append(f"{spec.key}: package integrity could not be checked")
                    continue
                if verified.code != 0:
                    first = redact(verified.output).strip().splitlines()
                    issues.append(
                        f"{spec.key}: package integrity check failed"
                        + (f" ({first[0][:120]})" if first else "")
                    )
        return ToolAudit(not issues, len(statuses), tuple(issues))
