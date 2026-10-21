# OpenCode Zen free quota recovery

The Zen free-model IP limiter raises `FreeUsageLimitError` when a daily bucket is exhausted and supplies a `Retry-After` duration to the next UTC day boundary. The bridge recognizes that error only when the selected model belongs to the OpenCode Zen provider and its model ID ends in `-free`.

On the first confirmed limit, the bridge records a provider-wide pause in SQLite and places the current task in the durable `retrying` state until the next 00:00 UTC. Tasks keep their session, prompt, attachments, and queue position. Before sending a request for a Zen free model, later tasks check the persisted pause and wait without invoking the model. Other models are not blocked. The queue polls at most once per minute, so work resumes automatically within 60 seconds of the reset even after a process restart.

Task concurrency has two independent guards: the worker pool is capped at three, and SQLite claim transactions refuse a fourth active lease across processes. Immediately before checking out work, the adaptive controller takes a fresh resource sample. It lowers the worker limit for memory, CPU, swap, or disk pressure, and refuses new claims if the resource sample fails. On a host with less than 1.5 GiB of RAM, the existing host policy limits admissions to one worker.

Upstream reset behavior: [OpenCode Zen daily IP rate limiter](https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/util/ipRateLimiter.ts#L7-L48).

## Credential-aware failover

`bridge.infrastructure.opencode.credential_pool` provides an arbitrary-size authenticated credential pool for credential-scoped failures. Credentials are configured outside Git and status snapshots expose only names and SHA-256 fingerprints, never secret values. A credential-specific quota/rate-limit can cool down only the affected credential, and authentication failures can disable only the invalid credential.

The pool deliberately does **not** rotate credentials for `zen_free_quota`. Upstream Zen selects the IP limiter for models that allow anonymous/free access, so changing an authenticated credential on the same host does not create a new host-IP bucket. The durable provider-wide pause remains the source of truth for that condition.
