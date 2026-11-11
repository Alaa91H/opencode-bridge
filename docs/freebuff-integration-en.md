# Freebuff CLI backend (English quick guide)

The bridge can route individual owners to the interactive **Freebuff CLI**
instead of OpenCode. OpenCode remains the default.

## Why a PTY harness

Freebuff has no headless mode today (`--print`, `--json` and ACP are upstream
requests). The bridge therefore stages the full instruction in
`<FREEBUFF_WORKSPACE>/.bridge/prompts/<run>.md`, starts
`freebuff --cwd <workspace>` in a pseudo-terminal, injects one short line
pointing at that file, and extracts the final answer from
`~/.config/manicode/projects/*/chats/*/chat-messages.json` (falling back to
`/export` JSON). See [ADR 0001](adr/0001-freebuff-cli-backend.md).

## Setup

```bash
npm install -g freebuff
freebuff login
freebuff --version   # must be >= 0.0.140
```

Add to the bridge `.env`:

```dotenv
AGENT_BACKEND=opencode          # opencode | freebuff | auto
FREEBUFF_BIN=/home/ubuntu/.local/bin/freebuff
FREEBUFF_CONFIG_DIR=/home/ubuntu/.config/manicode
FREEBUFF_WORKSPACE=/home/ubuntu/github-workspaces
FREEBUFF_MODEL_WRITE=0
FREEBUFF_TRUST_AGENTS=0
```

Restart the service, then use `/backend` in Telegram. Selection is per owner:

```text
/backend
/backend freebuff
/backend auto
/backend opencode
```

## Operational notes

- Session key is `(owner, "freebuff", workspace)`. The chat id is stored after a
  successful run and resumed with `--continue <chat-id>`.
- The child receives a minimal environment plus the session D-Bus/keyring
  variables needed by Freebuff login: no Telegram token, provider keys or proxy
  variables.
- `log.jsonl` is never read; it may contain a plaintext account key.
- `cwd` is confined to `FREEBUFF_WORKSPACE`; process groups are killed on
  cancel/timeout.
- Output is ANSI-stripped, redacted (`cb_…`, `sk-…`, `ghp_…`, Bearer) and
  length-bounded before delivery.
- Failure is closed: unsupported versions, missing login and unknown TUI shapes
  surface a clear Arabic message instead of guessing.

## Rollback

`/backend opencode`, or set `AGENT_BACKEND=opencode` and restart. No database
rollback is required; the new tables are additive.

Full Arabic guide: [freebuff-integration-ar.md](freebuff-integration-ar.md).
