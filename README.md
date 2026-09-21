<div align="center">

# OpenCode Bridge

### Arabic Telegram control plane for a locally hosted OpenCode development agent

<img src="https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
<img src="https://img.shields.io/badge/OpenCode-Agent%20Bridge-111827?style=for-the-badge" alt="OpenCode" />
<img src="https://img.shields.io/badge/Interface-Telegram-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram" />
<img src="https://img.shields.io/badge/Deployment-systemd-FCC624?style=for-the-badge&logo=linux&logoColor=111111" alt="systemd" />

</div>

---

## Overview

**OpenCode Bridge** connects an Arabic Telegram interface to a locally hosted **OpenCode** agent and turns it into a controlled development and operations workflow for Git/GitHub repositories.

The project is built around persistent sessions, queued development tasks, guarded shell access, adaptive resource management, model selection, progress reporting, and auditable server-side operation.

It is intentionally designed so the production server acts as an **agent execution environment**, not a build machine.

## Core Capabilities

- Arabic Telegram interface for interacting with OpenCode.
- Persistent OpenCode conversations and task state.
- Two-line free-points usage header above every final agent response, with persistent daily local accounting.
- Durable task queue backed by local storage.
- Multi-repository Git/GitHub development workflows.
- Attachment intake with bounded storage handling.
- Live task progress and persisted activity reporting.
- Research-oriented commands for search, deep research, comparison, verification and source inspection.
- Dynamic model catalog reconciliation.
- Automated free-model scouting with guarded selection rules.
- Adaptive task-worker concurrency based on live host pressure.
- Boot-persistent ZRAM sized by default to 50% of physical RAM, with safe resize/defer behavior.
- Host health scoring and resource diagnostics.
- Watchdog and maintenance reporting.
- Daily guarded system/package cleanup and upgrades.
- Validated fast-forward self-update from `Alaa91H/opencode-bridge` with pre-deployment tests.
- Daily autonomous post-maintenance agent audit and strongest-free model selection.
- Structured, redacted audit logging.
- GitHub CI integration for verification and release workflows.
- systemd-based deployment for long-running services.

## Safety & Execution Guardrails

The checked-in OpenCode policy blocks high-risk or inappropriate server-side operations, including:

- package installation and dependency bootstrap commands;
- local application build/compile commands;
- destructive Git cleanup/reset operations;
- force pushes;
- repository deletion and direct PR merge commands;
- shutdown, reboot, halt and poweroff commands;
- access to common secret-bearing files such as `.env`, private keys and PEM files.

OpenCode is configured to bind to **localhost** and authentication secrets are expected to remain in a local `.env` file rather than the repository.

## Adaptive Resource Control

The bridge continuously evaluates host pressure before admitting new queued work.

Signals include:

- available memory;
- Linux memory PSI;
- normalized CPU load;
- swap pressure;
- free disk capacity.

Concurrency can contract under pressure and recover gradually using stabilization and cooldown logic. Running tasks are not cancelled merely because the worker limit is reduced.

The project also maintains a shadow recommendation path so alternative worker policies can be evaluated before they are allowed to affect production behavior.

## Model Management

The bridge includes a persistent model manager and agent scout that can:

- inspect the live OpenCode agent/model catalog;
- research the strongest currently zero-cost OpenCode Zen model every day for agentic software development;
- apply the highest known reasoning variant announced by the selected model's live catalog metadata;
- keep Muse Spark 1.3 Contributor Free as a safe configured fallback instead of a permanent pin;
- reject stale, unavailable, or newly paid selections before applying them;
- persist validated decisions across restarts;
- fall back deterministically when external research is unavailable.

Because OpenCode Zen does not currently expose an authoritative remaining-free-quota counter, the Telegram bridge keeps a persistent local estimate in `runtime/free-points.db`. `OPENCODE_FREE_DAILY_POINTS` controls the local daily ceiling (default `200`). Each completed agent request is measured from the number of new assistant/model turns created by OpenCode, so tool-call loops and automatic retries are reflected in the command's consumption instead of being flattened to one point.

## Architecture

| Component | Responsibility |
| --- | --- |
| `bot.py` | Telegram application, commands and orchestration |
| `opencode_client.py` | OpenCode server communication |
| `task_service.py` / `task_queue.py` | Queued task execution and persistence |
| `session_store.py` | Persistent conversation/session state |
| `model_manager.py` | Live model reconciliation and defaults |
| `agent_scout.py` | Periodic free-model research and validation |
| `adaptive_workers.py` | Resource-aware worker admission |
| `progress*.py` | Live and persisted progress reporting |
| `attachments.py` | Bounded attachment handling |
| `audit_log.py` | Structured audit trail |
| `github_ci.py` / `ci_plugin.py` | GitHub CI workflow integration |
| `deploy/` | systemd service definitions and deployment assets |

## Configuration

Copy the example environment file and keep the real `.env` only on the server.

Important settings include Telegram credentials/allow-lists, OpenCode host/authentication, model defaults, worker limits, attachment limits, and optional Telegram proxy configuration.

See:

- [`.env.example`](.env.example)
- [`opencode.json`](opencode.json)
- [Arabic deployment guide](DEPLOYMENT_AR.md)
- [Release process](RELEASE_PROCESS_AR.md)
- [Changelog](CHANGELOG.md)

## Deployment

The intended deployment model keeps OpenCode bound to `127.0.0.1`, runs the bridge under user-level systemd services, and stores runtime credentials locally with restricted permissions.

The server should receive already-prepared source/configuration changes; builds and dependency installation are deliberately blocked by policy.

For the current deployment procedure, read **[DEPLOYMENT_AR.md](DEPLOYMENT_AR.md)**.

## Verification

GitHub Actions is used as the source of truth for repository verification, including syntax/tests, OpenCode configuration checks, semantic-version validation and release publication where configured.

## Language

The operator-facing Telegram workflow and deployment documentation are primarily **Arabic**, while code and technical identifiers remain structured for maintainability.

---

<div align="center">

**A guarded bridge between conversational development and a real OpenCode execution environment.**

</div>
