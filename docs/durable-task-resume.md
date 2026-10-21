# Durable task resumption

Queued development tasks persist in the existing SQLite database. This policy is
enabled automatically; it does not require a new scheduled job or a second bot.

- A checkpoint stores the OpenCode session, selected model and message identity
  before external work starts. The original task ID, prompt and attachments stay
  in the queue. Session history and the repository contain the work already done.
- HTTP 402/429 and explicit provider quota/credit errors enter `retrying` without
  an attempt limit. `Retry-After`, including HTTP dates, controls the next eligible
  attempt. Without a reset hint the bridge retries after about one minute.
- Workers inspect the queue at intervals no longer than 60 seconds. A future
  retry timestamp never becomes a long blocking sleep. The bridge does not assume
  that a particular plan renews every day or that local server health proves
  provider credit availability.
- Asynchronous OpenCode submission uses a persisted message ID. On restart or
  network ambiguity the worker checks for that message and reconnects to its
  result. It does not send the same prompt again when the message already exists.
- Confirmed provider failure allows a new continuation message in the same
  session. The continuation instructs the agent to inspect repository, issue, PR
  and CI state before acting and to continue only unfinished work.
- Deferred tasks block later tasks from the same owner, including higher-priority
  tasks. Independent owners remain eligible. Cancelling a deferred task explicitly
  releases that owner's queue. `/tasks` shows deferred tasks.
- A heartbeat renews execution leases every 30 seconds. Restart recovery queues
  leased and checkpointed running tasks; uncheckpointed legacy running tasks
  remain failed for manual review. Deferred timestamps survive restarts.
- Inputs and working files are retained while work is active/deferred and are
  cleaned only after terminal completion, failure or cancellation through the
  normal attachment lifecycle. Recurring tasks clear their checkpoint between runs.
- Ordinary transport/server failures have a finite retry budget. Authentication,
  invalid configuration and permanent errors remain visible failures, rather than
  retrying forever. Cancellation is respected during error handling.

Run all automated verification in GitHub Actions. No build or test is required on
the production server. Server deployment consists of updating verified files and
restarting the existing service. Both services must remain enabled and user
lingering enabled to survive logout and reboot.

The bridge cannot promise exactly-once external side effects across an arbitrary
process crash: repository and GitHub state must be reconciled before repeating an
operation. It also cannot restore a provider session that was deleted externally.
Those conditions are reported instead of inventing completed work.

OpenCode's asynchronous submission and message lookup endpoints are documented at
https://opencode.ai/docs/server/#messages.
