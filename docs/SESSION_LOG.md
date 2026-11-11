# Session Log

Chronological record of substantial implementation, verification and release
work. This file is intentionally concise; Git history contains the full diff.

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
