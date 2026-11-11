# Session Log

Chronological record of substantial implementation, verification and release
work. This file is intentionally concise; Git history contains the full diff.

## 2026-11-11 — Container-safe systemd hardening

- Added `bridge/infrastructure/deployment/systemd_hardening.py`: it probes each
  optional hardening directive through a transient user `systemd-run` service
  and returns the largest ordered subset that starts on the host.
- `systemd.py` now writes or removes a generated `20-hardening-compat.conf`
  automatically. Only rejected directives are disabled; `NoNewPrivileges=yes`
  stays in the unit and a fully capable host receives no fallback file.
- `scripts/deploy.sh` now regenerates units for the restored release during
  rollback, preventing the old-code/new-unit mismatch that kept the service down
  after the first container deployment attempt.
- Added tests for the supported and unsupported probe paths, generated-file
  cleanup, and rollback ordering; documented the probe in the Arabic deployment
guide and this architecture record.

### Deployment verification (server `130.61.123.50`)

- Removed the manually created `99-container-compat.conf` and generated
  `20-hardening-compat.conf` with the working-tree probe (run over SSH stdin, so
  no helper file was left on the host). The generated file disables only
  `ProtectClock`, `ProtectKernelLogs` and `ProtectKernelModules`; the other five
  optional directives and `NoNewPrivileges=yes` remain active.
- Redeployed from `v3.63.2` to `v3.65.0` through `scripts/deploy.sh`; the final
  identity reports `v3.65.0`, commit `ca58eaf`, and deployed_at
  `2026-11-11T09:59:26Z`.
- Verified both services active, `NRestarts=0`, no failed user units,
  OpenCode health true (`1.18.31`), queue clear, and only one rollback archive
  (`v3.63.2`) with a valid checksum.
- The published `v3.65.0` tag predates the probe code; the fix shipped in
  `3.65.1`, and the generated drop-in bridged the `v3.65.0` verification.

### Release 3.65.1 (2026-11-11)

- Release commit `3baa230` raised `VERSION` from `3.65.0` to `3.65.1`.
- `main` Build and Release (`43001010328`) and Python Compatibility
  (`43001011188`) succeeded; the verified workflow (`43001161674`) created
  annotated tag `v3.65.1` (tag object `c90a187`) targeting `3baa230`.
- The tagged build (`43001191347`) published the GitHub Release at
  `2026-11-11T10:10:28Z` with `opencode-bridge-v3.65.1.tar.gz` and
  `SHA256SUMS`.
- Deployed the tag through `scripts/deploy.sh`; identity reports `v3.65.1`,
  commit `3baa230`, and deployed_at `2026-11-11T10:11:34Z`.
- Verified both services active, `NRestarts=0`, no failed units, OpenCode
  health true (`1.18.31`), Telegram `Application started` at `10:12:08`, no
  post-start errors, and the host probe disabled only `ProtectClock`,
  `ProtectKernelLogs` and `ProtectKernelModules` while keeping
  `NoNewPrivileges=yes`.
- The only retained rollback archive is the verified `v3.65.0` archive
  (`opencode-bridge-ca58eaf96b15-20261111T101100Z.tar.gz`); the older
  `v3.63.2` archive was removed after the new archive passed checksum
  verification.

## 2026-11-11 — Freebuff backend and 3.65.0 consolidation

### Implementation

- Added `bridge/domain/agent/` contracts, events, errors and capabilities.
- Added `bridge/infrastructure/freebuff/`: binary probe, PTY runner, bounded ANSI
  screen model, defensive `chat-messages.json` reader, extractor and
  `FreebuffBackend`.
- Added `BackendRouter`, `/backend`, per-owner preferences and additive
  `backend_sessions`/`backend_preferences` tables.
- Wired `AgentService`, `TaskExecutionService`, `TaskApplicationService` and the
  Telegram model command to route through the selected backend.
- Added owner-scoped event hubs and abort routing so concurrent owners cannot
  terminate or consume each other's runs.
- Added a unique staged-prompt marker to select the correct Freebuff chat even
  when multiple runs share a config directory.
- Kept Freebuff disabled by default and OpenCode behavior unchanged.

### Verification

| Command/check | Result |
| --- | --- |
| `unittest discover -s tests/freebuff -t .` | 48 passed, 2 skipped |
| Core affected tests | 27 passed |
| WSL Linux PTY test with simulated TUI | 1 passed |
| `ruff` on touched files | Passed |
| `compileall` | Passed |
| Full local suite | 1379 tests, 13 failures, 179 errors, 14 skipped |
| Pre-change HEAD baseline | 1332 tests, 13 failures, 179 errors, 12 skipped |

The full-suite failure counts match the baseline exactly. Remaining local
failures are Windows environment issues (symlinks, POSIX permissions, timezone
data and host-service tests), not Freebuff regressions. Linux CI is the release
gate.

### CI and repository maintenance

- Fixed the pre-existing `scripts/render_telegraph_digest.py` lint violations.
- Fixed the unquoted path handling in `scripts/render_digest_wrapper.sh`
  (`SC2046`).
- Merged the reviewed Dependabot action pin update and npm plugin update.
- Archived 25 superseded remote branches with `-s ours` merge commits after
  confirming `main` carries the newer implementations; their commits remain
  reachable as merge parents.
- Consolidated documentation: added `AGENTS.md`, architecture, status, roadmap,
  decisions, session log and a new docs index; removed obsolete T-series and 2.0
  planning pages.
- Replaced the old documentation-as-code tests with checks for the current docs
  set and valid relative links.

### CI evidence (commit `5b81669`)

- Build and Release: run `42995712863` — success.
- Python Compatibility (3.12/3.13/3.14): run `42995713078` — success.
- Verified version release: run `42995895201` — success with no version change,
  correctly leaving the release untagged until the version bump.
- Remote branch cleanup completed: 26 non-`main` heads were deleted after the
  consolidated push; `main` is the only remaining remote and local branch.

### Release evidence (commit `ca58eaf`)

- Build and Release (`main`): run `42996065610` — success.
- Python Compatibility: run `42996065550` — success.
- Verified version release: run `42996241405` — success.
- Tagged Build and Release: run `42996264181` — success.
- Annotated tag `v3.65.0` (tag object `06dc280`; commit `ca58eaf`) was created
  only after both required `main` gates passed.
- GitHub Release published at `2026-11-11T09:35:14Z`:
  <https://github.com/Alaa91H/opencode-bridge/releases/tag/v3.65.0>.
- Release assets: `opencode-bridge-v3.65.0.tar.gz` and `SHA256SUMS`.
- The release commit raised `VERSION` from 3.64.0 to 3.65.0; the final
  documentation update does not change `VERSION` and therefore creates no new
  tag.

### Known follow-up

- Validate the real Freebuff CLI on a Linux host with a logged-in service-user
  account. The local PTY test used a deterministic fake TUI and cannot prove
  vendor-side login/quota behavior.
