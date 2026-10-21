# Security Policy

## Reporting a vulnerability
Please **do not open a public issue** for suspected vulnerabilities.

Use GitHub's private **Report a vulnerability / Security advisory** flow when available. If unavailable, contact the maintainer privately through a contact method listed on the GitHub profile.

Include the affected version/commit, impact, reproduction steps or proof of concept, relevant environment information, and redacted logs when possible.

Never include API tokens, Telegram credentials, stream keys, private endpoints, signing material, or other secrets in a report attachment.

Security fixes are evaluated against the current default branch. Maintained releases may also receive fixes when practical. Please allow reasonable time for triage and remediation before disclosure.

## Telegram control-surface threat model

- **Direct shell (`/exec`, aliases `/sh` `/shell` `/term`)**: owner-only in a private chat (`_is_control_owner`: allowlisted owner + `private` chat type). Every invocation is audit-logged with the full command; output is secret-redacted, time-bounded (120s) and size-bounded (256KB read, 12k chars shown). It runs as the bridge service user via `bash -lc` — full shell semantics, but only that user's OS privileges. By explicit owner design it bypasses the build/hardline request guards that constrain agent tasks; commands matching destructive patterns instead carry a ⚠️/ℹ️ advisory banner (never a refusal) recorded in the audit entry. Treat the owner account as a privileged SSH equivalent: protect it with the same care.
- **Host controls (restart/logs/reboot)**: fixed argument vectors only — caller input is ever an allow-list key, never shell text. The single escalation (`systemctl reboot`) additionally requires a separately installed sudoers rule.
- **Attachments are untrusted data**: the user caption is the only instruction source; prompt-file (`.md`/etc.) inlining applies solely to files the owner explicitly queued for execution. Paths, sizes, and SHA-256 are re-validated before any worker sees them.
- **Downloads/mirrors**: public-host validation (SSRF guard) on every URL and redirect hop before any byte is fetched; mirrors only ever handle files inside managed directories.
- **Audit logs** redact credential-shaped values and rotate at 10MB; they must never receive message content.
