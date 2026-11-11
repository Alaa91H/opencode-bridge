# Roadmap

This roadmap describes intended work, not commitments. Security and release
correctness take precedence over feature velocity.

## Now — 3.65.0 release

- [x] Add the `AgentBackend` abstraction and maintain OpenCode as default.
- [x] Implement the Freebuff PTY harness, routing, additive storage and
  per-owner `/backend`.
- [x] Add owner isolation, bounded output, secret redaction and fail-closed
  errors.
- [x] Add regression tests, ADR and Arabic/English operator guides.
- [x] Merge Dependabot updates and archive superseded branches.
- [x] Consolidate documentation around architecture, status, decisions and
  session history.
- [ ] Green Linux CI on the consolidated `main`.
- [ ] Publish the immutable `v3.65.0` tag and GitHub Release.
- [ ] Delete all non-`main` remote branches after the merge history is pushed.

## Next — production validation

- [ ] On a Linux host, run `freebuff login` as the service user and verify the
  `/backend` status probe.
- [ ] Execute a real text task, a task with an attachment, a cancellation and a
  `--continue` rerun against the installed Freebuff CLI.
- [ ] Confirm quota/login errors are user-safe and do not leak `log.jsonl`
  content.
- [ ] Record the exact CLI version and screen-shape result in
  `SESSION_LOG.md`.
- [ ] Deploy `v3.65.0` through `scripts/deploy.sh` and run the documented
  rollback once in staging.

## Later — transport and observability

- [ ] Add `FreebuffAcpTransport` behind `AgentBackend` when the upstream ACP
  work is released, keeping Telegram and queue contracts unchanged.
- [ ] Add bounded backend metrics to `/metrics`: per-backend runs, failures,
  cancellation, duration and refusal reasons.
- [ ] Revisit model selection once Freebuff exposes a stable programmatic model
  API; keep `FREEBUFF_MODEL_WRITE=0` by default.
- [ ] Evaluate multi-account Freebuff routing only after isolation and quota
  semantics are proven.

## Maintenance

- [ ] Keep GitHub Actions pinned to reviewed immutable commits.
- [ ] Keep the quality gate green; fix root causes instead of suppressing rules.
- [ ] Keep `CHANGELOG.md`, `PROJECT_STATUS.md` and `SESSION_LOG.md` current for
  every release.
- [ ] Delete stale documentation instead of letting it contradict the code.
- [ ] Revisit the Windows test environment only if it becomes a supported
  release platform; Linux remains authoritative.

## Non-goals

- Running builds or installing dependencies on the production host.
- Posting raw tool logs, reasoning, prompts or provider payloads to Telegram.
- Reading or uploading Freebuff `log.jsonl`.
- Silent fallback from an explicitly selected backend to a different one.
- Rewriting released tags or force-pushing `main`.
