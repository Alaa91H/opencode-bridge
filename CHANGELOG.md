## [3.57.0] - 2026-10-18

### The test suite was writing into the deployment it was testing

The acceptance sweep's only failing check on 3.56.0 was the working tree, and the debris was ours: two directories inside the release tree, `HEAD/` and `opencode-serve.service/`, holding nothing but `SKILL.md` fixtures. The git stand-in in `tests/test_skill_sync.py` copied its fixture for **every** command it was handed, so `git rev-parse HEAD` created a directory called `HEAD` and the systemctl restart created one named after the service.

- `_FakeGit` now answers with a copy only for commands that actually place a tree on disk, and reports the commit for the rest. It was also lying about what those commands do.
- Running the suite where the product lives is now side-effect free, which is what made the check meaningful in the first place.
- The debris was removed from the deployment: ten skill fixtures, nothing else. A guard refuses to delete either path unless it holds nothing but `SKILL.md`.
- Tests record the working directory before a full sync and reload, and require it unchanged afterwards.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.56.0] - 2026-10-18

### The collapse guard now catches the shape it was most often missing

Writing the acceptance sweep found a hole: the commonest collapse is the same two or three words repeated inline along one long line. `_PHRASE_LOOP` builds its phrase from `\S*`, which cannot span a space, so a multi-word loop matched nothing; `_REPEATED_LINE` cannot see it either, because the repetition never reaches a newline.

- `_inline_phrase_loop` finds it with a plain period scan rather than a regex. A pattern spanning arbitrary words is exactly where backtracking turns a 4000-character page into a stalled delivery.
- A repetition must still be at least 200 characters and five copies, and the repeated chunk must contain real letters, so a short refrain, a list that reuses a word, and a long normal sentence all pass untouched.
- Verified against the answers this host has actually delivered, not invented samples: **zero disagreements** with the shipped guard. One stored answer really had collapsed — `Allocateallocate_html_docs…` repeated 3212 times — and it was already being caught, at exactly the same offset.
- A timing test keeps the scan under three seconds on a 120-paragraph clean answer.

### `scripts/acceptance.py` — the sweep lives in the repository

Verifying a deployment had been an ad-hoc script rebuilt by hand each time, which is how a report once shipped with an empty version number, a raw JSON line and a visible `&gt;`. It is now a repository tool with twenty checks that read the live deployment: identity, git head, working tree, services, agent health, watchdog verdict, queue, the three permission blocks, capability posture, systemd's live config, the agent's skill list, digest pages, the collapse guard, plain-answer rendering, the skills repository, the two-way client protocol, host control and the reload path.

- The agent health probe loads the deployment's own `.env` and sends basic auth. Without the password the server answers 401 and a healthy service reads as broken.
- The report is built as Markdown and rendered **once** through the product's renderer, then sent as HTML. `--report` delivers it to the owner's chat.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.55.0] - 2026-10-18

### The watchdog stopped calling a healthy host degraded

Deploying 3.54.0 exposed the alarm this host has been living under: every five minutes the watchdog reported `degraded / score 85 / high memory pressure` while the host reported 951 MiB of RAM, 255 MiB available, **5082 MiB of swap with 4778 MiB free, and memory PSI at 0.00**. Nothing was wrong. Free memory is not pressure: a host that swaps freely keeps that figure low precisely *because* nothing is stalling.

- Pressure is now reported when there is nowhere left to put a page — swap below 15% free — rather than when the free-memory number alone is small. The thresholds themselves are unchanged, so a host that really is out of room still alarms exactly as before.
- A host that reports **no swap** is treated as having no room, not as having unlimited room. An unknown must never be read as a green light.
- Actual stalls are still reported by the PSI rules, and a stalling host is no longer counted twice: `high memory pressure` and `memory stalls detected` can no longer both fire for one condition.
- The protective guard on automatic restarts is deliberately untouched. It refuses to act when memory is critically low, and that is the safe direction to be wrong in.
- Tests carry the exact live numbers that produced the false alarm, so the regression cannot return silently.

An alarm that fires when nothing is wrong is not a nuisance, it is a defect: it is how the owner stops reading the watchdog at all.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.54.0] - 2026-10-18

### `/skills` no longer shows its own markup as words

`/skills` built a finished Telegram HTML page and then handed it to the Markdown renderer, which escaped the markup the page already contained, and the result went out as plain text. The owner read `<b>🧠 مهارات الوكيل (3)</b>` and `&gt;` as literal words on a screen that had been rendering every other answer as clean text. The same mistake in an ad-hoc report showed an empty version and a raw JSON line, which is how the defect was found.

- A page that is already HTML is now sent with `parse_mode="HTML"`: `send_html` in `rich_delivery`, and `_safe_html_reply` in `bot.py` which retries like every other reply and degrades to the plain twin only if Telegram refuses the format.
- `html_to_plain` produces that twin by stripping tags and unescaping entities, so a rejected page still reads as the original words rather than a wall of `&amp;`.
- `SkillCommands` takes an optional `reply_html` seam and no longer imports the Markdown renderer at all. After this change the Markdown renderer has **no production caller left**; every other page is either Markdown rendered once, or plain text, which is why this could not happen twice.
- Checked the other HTML-shaped renderers (`schedule_page`, `model_page`): they return plain text and are unaffected.

### The preview mirror had drifted from the repository

`/home/ubuntu/preview` — the tree used to iterate locally instead of spending a CI round trip — was an older revision of `tests/test_v2_baseline_contract.py`, so the quality gate reported a `B007` that does not exist in the repository. A test environment that is not the repository cannot be trusted to catch anything, so it is now rebuilt from the deployed tree by `resync-preview.sh`, and a mirror-wide trailing-newline repair runs after each copy. `quality=PASS` on the mirror now means the same thing it means in CI.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.53.0] - 2026-10-18

### The collapse guard no longer cuts a correct answer for containing a separator

The acceptance sweep caught the guard doing more harm than the thing it was built for. A finished report whose last section quoted the 70 hyphens `unittest` prints between its results was declared collapsed: it was truncated mid-answer, the owner was told the model had broken down, and the full text was attached as a file. The answer was flawless — the guard had matched a line of punctuation.

- A single stuck character must now be a **word** character, repeated 120 or more times. `wwww…` and `0000…` are still caught; a rule made of `-`, `=`, `*`, `_`, `#` or `~` is not.
- Decorative rule lines are masked to spaces of the same length before anything is measured, so a markdown rule, a table border or a test separator cannot match, and every later offset still points at the same place in the original answer.
- The repeated-phrase rule now requires real words and 200 characters of total match, so ordinary prose cannot trip it.
- The repeated-line rule requires at least twenty characters of content.
- Verified against the real report that was cut: it now renders whole, with no note and no attachment.
- Tests: a unittest separator, a markdown rule and a table border each pass through untouched, a real collapse beside a separator is still found, and masking preserves offsets.

### Host hygiene: the deployment scratch space was full

`/tmp` on this host is a 476 MiB tmpfs and had reached 376 MiB with 189 stale runtime scratch files, which made an unrelated transport test fail with `Disk quota exceeded` — the kind of environment failure that gets written off as "pre-existing" and then hides real defects. Reclaimed to 33 MiB without touching anything the bridge needs.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.52.0] - 2026-10-18

### A full disk and a full quota no longer report the same fault

Found by running the whole acceptance sweep against the live deployment: the managed-download test failed on the host with "Managed download storage is full" while the download index held zero entries and the disk had 37 GiB free. Two different faults were collapsed into one sentence, so an operator is told to go and free a quota bucket that was never the problem — and the real cause, a small volume with a fixed 256 MiB reserve, stays invisible.

- `_transfer_budget` now separates the two limits: an exhausted quota says how much is stored ("Managed download storage is full (0.0 GiB used)"), and an exhausted filesystem says how much room is left in MiB.
- The disk headroom reserve scales down to a twentieth of the free space on small volumes instead of a flat 256 MiB, so a 500 MiB filesystem is no longer declared unable to start any download.
- The budget helper creates its own directory before measuring, so no caller can reach `disk_usage` on a path that does not exist yet.
- Tests: a full quota and a small volume each produce their own message, and a small volume still accepts a transfer.

### Acceptance sweep on the live deployment

20 checks against the deployed tree, not the preview: version, deployment identity, git head matching the deployed commit, clean working tree, both services active, agent health, watchdog, queue state, all three live rule blocks identical, capability posture, the systemd drop-in still pointing at the runtime config, the agent's skill list, digest rendering, the collapse guard, an untouched plain answer, installed skills, the skills repository, the client speaking the agent's protocol, host control, and the agent reload path. All twenty pass.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.51.0] - 2026-10-18

### The agent can now create a repository and build a project

Reported as "it cannot do the job when I ask it to create a repository and build something". Reproduced with the owner's own wording, and the cause turned out to be three layers deep — none of them visible from the repository alone.

- **The profile named "full access" was the strict one.** The live agent configuration is `config/opencode.full-access.v1.json`, copied to `runtime/opencode-config/opencode.json` and pointed at by a systemd drop-in. The repository's own `opencode.json` is **not** what the agent reads. Editing the root file therefore changed nothing at runtime, which is why earlier attempts appeared to do nothing.
- **One stale ruleset was being merged after the current one.** The profile carries its bash map three times — once globally, once per agent — and the copies had drifted. The engine merges them into 245 rules and applies the last match, so the trailing `bash *: deny` shadowed every allow. The agent diagnosed this itself: "the permissive entries are always shadowed … the strict one is what actually gets enforced". A fresh session did not help, because the source was the config, not the session.
- **`"ask"` is a denial in a headless deployment.** With no client attached, an approval request has nobody to answer it and resolves to a refusal. A rule set to ask is therefore a rule set to deny, which is why the permission cards from 3.49.0 never appeared for this.

The fix, verified end to end on the owner's server:

- `bash` now defaults to **allow**, with git, `gh` (including `gh repo create`), every runtime and build tool, and `systemctl`/`journalctl` allowed outright. What stays refused is the irreversible and credential-bearing set: force push, `reset --hard`, `clean`, `checkout --`, `sudo`, `su`, `gh auth login/token/refresh`, `gh repo delete`, reboot/shutdown, `mkfs`, `dd`, `userdel`, `passwd`, and piping a download into a shell.
- Secret files stay unreadable and unwritable: `*.env`, `*.env.*`, `*.pem`, `*.key` remain denied for both `read` and `edit`.
- All three rulesets are now generated from one list, and a new test fails the build if they ever drift again — that drift is what made the agent refuse `python`, `git init` and `gh repo create` with no trace in the repository.
- The agent prompts no longer forbid builds, installs or repository creation. `prompts/full-access-operator-ar.md` and `prompts/development-agent-ar.md` now say the server is the workspace, that a requested new repository is created rather than declined for lack of a workspace block, and that a policy refusal is reported in one line rather than worked around.

Proven on the live server, not asserted: the agent ran `python3`, `git` and `mkdir` successfully, then created `Alaa91H/bridge-build-probe`, wrote a real package with three unit tests, **ran the suite and watched it pass**, committed, pushed, and verified the remote branch by SHA — reporting the URL, the commit and the verbatim test output. The probe repository was deleted afterwards.

### Upgrade notes

- No dependency changes, no migrations. The permission posture changes by design: the agent may now build, install dependencies, create repositories, and manage systemd user services. The deny list above is what still stands between it and an irreversible act.

## [3.50.0] - 2026-10-18

### The agent's skills now arrive from their repository, on their own

Found by reading the Agent Skills documentation against this host: OpenCode discovers skills from `~/.config/opencode/skills/<name>/SKILL.md` and **reads that directory when the server starts**. The bridge knew nothing about it, so every skill pushed to `Alaa91H/portable-agent-skills` was invisible to the agent until someone restarted the server by hand. Two facts followed and shaped the whole design: writing files is not enough, and a skills repository lands inside the agent's own instruction path, where a file becomes behaviour.

- New `bridge/services/skill_sync.py` clones the repository shallowly, validates every skill against the published Agent Skills rules — folder name matching `^[a-z0-9]+(-[a-z0-9]+)*$`, frontmatter `name` equal to the folder, a 1–1024 character description, no symlinks anywhere inside, and hard caps on file size, skill count and batch size — and only then publishes, all-or-nothing.
- Publishing is a directory rename with a rollback: the live skill is moved aside, the new one moved in, and a failure puts the old one back. The agent never sees a half-written skill.
- A skill removed upstream is removed here, but the bridge only ever deletes skills it published itself: a hand-written skill and the built-in one are untouched, and an **empty repository never deletes the installed library** — it reports "the repository has no skills yet" and leaves the agent working.
- A failed fetch changes nothing and says so: `/skills` keeps showing the installed library and the last error.
- When a sync actually changes something, the bridge restarts the agent server so the new skills are live, and only then. A no-op sync never touches the running server.
- Automatic: a background loop syncs every six hours (`BRIDGE_SKILLS_SYNC_MINUTES` to change), source configurable with `BRIDGE_SKILLS_SOURCE`.
- Telegram: `/skills` lists the library, its freshness, the commit it came from and anything refused; `/skills sync` forces a refresh and reports exactly what was installed, updated, removed or refused. Both are audited.
- Tests: `tests/test_skill_sync.py` (18 tests) — validation refusals, symlinks, no-op syncs, upstream removal, the empty-repository guard, an unreachable repository, a hand-written skill surviving a sync, concurrent syncs, and the reload contract. 1167 local tests, only the 12 pre-existing environment failures.

### Note for the operator

`Alaa91H/portable-agent-skills` is currently empty. Push folders shaped as `<skill-name>/SKILL.md` with `name` and `description` frontmatter, and they will be installed automatically on the next sync, without restarting anything by hand.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.49.0] - 2026-10-18

### The agent can ask, and Telegram can answer — the conversation is now two-way

Audit of the live OpenCode server against the bridge: the client exposes 162 endpoints, and the bridge used one direction of them. Two of them were simply not connected. `permission.asked` produced a status line saying the agent was waiting for a decision, and no button, no command and no message could answer it; the `question` tool produced nothing at all — when the agent asked the operator something, the question never reached Telegram at all. Both then burned the task's whole budget until the server's own timeout, and the audit recorded a perfectly healthy task.

- New client calls for the missing half: `list_permission_requests`, `reply_permission` (`once` / `always` / `reject`), `list_questions`, `reply_question`, `reject_question`. Identifiers are validated against the server's own `per*` / `que*` prefixes, so a request id from one route cannot be replayed on the other.
- A permission request now arrives as a card: the action in plain Arabic, up to four targets, and three buttons. The agent's `metadata` is never read into the card — it can carry command arguments, and a chat is not a channel for them.
- A question now arrives as a card with the agent's own options as buttons. One tap answers a single-choice question; a multi-select question toggles and waits for an explicit confirm; `custom: true` adds a text answer, delivered by replying to that card.
- Both server generations are read (`permission.asked` and `permission.v2.asked`, `question.asked` and `question.v2.asked`).
- Every answer re-checks that the actor is allowed, because `CallbackQueryHandler` bypasses the `@authorized` decorator. A decision the server has already forgotten says so in plain words instead of appearing to succeed, and a card that cannot be delivered never fails the task.
- A reply to a question card is registered above the general text handler: answering the agent must never start a second task.
- Pending state lives in `bridge/telegram/interactions.py` with a one-hour TTL, so a card whose request the server forgot expires rather than answering a later, unrelated task.
- Tests: `tests/test_agent_interactions.py` (28 tests) — both event generations, payload forgery and cross-route replay, a stranger's press, a forgotten request, a raising button, multi-select toggling, text answers, and that `metadata` never reaches the chat. 1149 local tests, only the 12 pre-existing environment failures.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.48.0] - 2026-10-18

### A collapsed answer is cut and explained, never delivered as repetition

Found by re-running the owner's digest request on the released build: the model wrote six good items and then collapsed into `wwwwww…` for the remaining 55,000 characters. The bridge delivered that faithfully — fifteen pages of noise in Telegram — because from its side the answer was long, successful and free of errors. Being faithful to a broken answer is not being correct.

- New `bridge/telegram/rendering/collapse_guard.py` finds where a repetition collapse starts: a character run of 60+, the same phrase back to back five times, or the same line repeated. Each pattern must be long enough to be certain, so `100%` and a table row that repeats a character survive untouched.
- The cut lands on the last coherent sentence, never mid-word, and the answer ends with a plain note: the answer stopped mid-way, here is the part that reads, the complete text is in the attached file.
- The guard runs inside `delivery_pages`, the single renderer every delivery path goes through, so no caller can deliver a loop by accident.
- A collapsed answer now always materializes `answer-full.md`, whatever its length: nothing the model actually wrote is thrown away, and the audit records `output_collapsed` with the delivery decision.
- Verified on the real answer: 63,794 characters over 22 pages became 7 pages with zero repetitions, cut at 8,179 characters — the six items that read, plus the note.
- Tests: `tests/test_collapse_guard.py` (12 tests) covering detection, boundary quality, untouched clean text, `100%`-style false positives, and that no page ever ships a loop; 1121 local tests, only the 12 pre-existing environment failures.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.47.0] - 2026-10-18

### A digest item arrives as a channel post, not a wall of text

Found by re-running the owner's own digest request end to end and reading what the bot actually sent: the per-item pages were correct, but each item was one dense paragraph — `التاريخ / التصنيف / المزود / ما يشمله / الحدود والشروط / طريقة الحصول / الرابط` all ran together with `[1]` markers no page could resolve and a rule dangling at the end.

- Every labelled field of a run-on line becomes its own bullet, so an item reads as a short list a channel would publish, not a slab.
- A headline that swallowed its own fields (`### 7. … الكلام**التاريخ:** …`) keeps the headline and hands the fields to the list, instead of a heading the size of a page.
- A lone labelled field joins the list rather than sitting between two tidy lists as prose.
- A field name is one or two words: `Mistral تطلق Timestral: نماذج…` is a title with a colon in it, not a field, and no longer gets its title bolded.
- Long prose is broken into paragraphs at sentence ends, so a 700-character paragraph no longer arrives as one dense block. A URL is never cut: a boundary only ever follows whitespace.
- `[3]` citation markers are dropped when — and only when — the answer really carries sources, decided once for the whole answer so no page disagrees with its neighbour. An answer whose sources are only markers keeps them.
- A page never opens or closes on a `────────────` rule; a rule inside a page stays.
- `«Rosalind Plan»:30` gets its missing space, while `12:30` is left alone.
- Verified live: the owner's digest now renders as 14 pages — a title, an executive summary, and one page per item with its own headline, field list, and source button, longest page 1,456 characters.
- Tests: `tests/test_readable_field_layout.py` (17 tests) plus the existing rendering and news-item suites; 1109 local tests, only the 12 pre-existing environment failures.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.46.1] - 2026-10-17

### A section label stops arriving as its own message

- A digest's `## عاجل` heading used to be emitted as a page holding only `▌ عاجل` before its first item, which reads as a delivery fault. A section heading is now held until real content follows it, and disappears into the item it introduces.
- The follow-up budget rose from 25 to 40 messages, so a 40-item digest arrives item by item instead of pointing at a file after 25. Overflow truth-in-delivery is unchanged.
- Tests: `tests/test_news_item_pages.py` (no page is only a section label), `tests/test_overflow_delivery.py` (budget raised to 40 pages).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.46.0] - 2026-10-17

### Each news item is delivered as its own page, like a global channel

- Audit finding: a 40-item digest arrived as 30 wall-of-text sections. A global news channel shows one item per message — headline, the labelled fields, the photo, and the source button — and that is what the owner asked for.
- New `bridge/telegram/rendering/news_pages.py` recognises a digest item by shape (a bold headline followed by bullets), so the section it sits under becomes that page's label, each item keeps only its own source link, and document order is preserved across items, ranked lists and prose.
- Labelled bullets now render as bold labels: `• ما حدث: …`, `• التفاصيل: …`, `• تحليل: …` — the shape every digest already used, finally readable.
- An item's own photo is sent as the lead image of that item's page instead of trailing the whole answer; photos that belong to no item still follow it.
- The follow-up budget rose from 8 to 24 messages, so a 40-item digest really does arrive item by item instead of stopping after nine and pointing at a file. Truth-in-delivery is unchanged: past the budget the remainder still materializes as `answer-full.md`.
- An answer that is not a digest is untouched: no item detection, no extra splitting, the same section pages as 3.45.
- Tests: `tests/test_news_item_pages.py` (one page per item, section label, bold field names, per-item source link, per-item photo, bold prose is not a headline, document order, plain answer unaffected).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.45.1] - 2026-10-17

### A document title no longer gets a page of its own

- A report that opens with `# title` then `## section` produced a first message holding nothing but the title, which reads as a delivery fault. A heading now starts a new page only when the current page already carries real content, so the title and its first section arrive together.
- The owner's own 95 KB daily digest renders as 30 topic pages instead of 31, with no empty page.
- Tests: `tests/test_markdown_html_rendering.py` (title stays with its first section).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.45.0] - 2026-10-17

### Answers arrive as formatted Telegram pages

- Audit finding: agent answers are Markdown, and Telegram renders none of it. `##`, `**bold**` and `-` bullets reached the owner verbatim, paragraphs ran together, and a long digest was cut on an arbitrary character boundary. The content was right; the presentation was not.
- New `bridge/telegram/rendering/markdown_html.py` renders an answer into what the client actually shows: titled sections with dividers, bold/italic/strike/inline code, `•` bullets, blockquotes, clickable sources, and real blank lines between blocks. HTML metacharacters are escaped, so comparisons and code survive.
- Paging is topic-aware: a heading starts a section, a section is never cut in half unless it alone exceeds a page, and a fenced code block is never split. Each page carries its own source buttons and a page counter, so a topic page is self-contained.
- Every page keeps a plain-text twin. A client that rejects HTML now receives the same readable page instead of a failed message, and the live status message is rendered the same way with the same fallback.
- The status message and the follow-ups agree on where the first page ends, so no topic is cut between them.
- Truth-in-delivery is unchanged: an answer too long for the page budget still materializes as `answer-full.md`, and the local Bot API attachment fallback added in 3.44.0 is untouched.
- Tests: `tests/test_markdown_html_rendering.py` (headings, inline styles, escaping, code blocks, topic-aware paging, per-page links, plain twin, HTML rejection fallback for both pages and the status message).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.44.0] - 2026-10-17

### Local Bot API attachments download from the server's own file endpoint

- Audit finding: with `TELEGRAM_API_MODE=local` the Bot API server keeps every upload at `0600` inside a `0750` directory owned by its own service user, so the bridge user cannot read the path `getFile` returns. python-telegram-bot then assumes a cloud path, requests a URL the local server does not serve, and the resulting `404` reached the owner as `InvalidToken: Not Found` behind the generic "unexpected problem while receiving the attachment" message. Every attachment failed on that host.
- `AttachmentStore` now streams the file from the local Bot API file endpoint (`<local_file_base_url><token>/<file_path>`) whenever the direct copy is impossible. That URL embeds the bot token, so it is never logged, and redirects are not followed so the token can never travel to another host.
- The per-attachment byte budget is enforced while streaming instead of after the whole body is buffered, and any partial file is removed on failure.
- A missing or expired local file is reported as an actionable "resend the attachment" message rather than a generic failure, so the owner knows what to do next.
- Tests: `tests/test_v2_telegram_transport.py` (endpoint fallback with the exact URL shape, expired-file cleanup with no leftover bytes, PTB download still untouched).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.43.0] - 2026-10-17

### Daily continuation only trusts an explicit completion mark

- Audit finding: a run ending with a courtesy sign-off could satisfy the completion check while work remained, so the daily continuation closed the day early.
- The completion mark must now be the whole last line, within a 24-character sign-off budget; hedge markers block a match, and the sentinel tail is checked for hedging before punctuation is stripped.
- Tests: `tests/test_daily_completion.py` extended (whole-line match, over-budget sign-offs, hedged and partial endings, delay floor).

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.42.0] - 2026-10-17

### Due-schedule promotion is coalesced across the pool

- Audit finding: every worker sweep could re-run due promotion for the same interval, so one due job produced repeated promotion queries under load.
- Promotion is now coalesced pool-wide per interval; a failed promotion is logged, resets the stamp, and the next sweep retries instead of waiting another full interval.
- Tests: `tests/test_promotion_coalescing.py`.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.41.0] - 2026-10-17

### Reboots and restarts join the host guard

- Completes 3.40.0: `HostController.reboot`, `restart_service` and `cleanup_now` consult the same `HOST_OPERATIONS` guard, so a restart or reboot cannot land in the middle of an upgrade.
- `reboot()` refuses before the sudoers probe, so a refused request runs no host command at all.
- Panel wording `host_busy` (ar/en) names the running operation instead of the generic retry error that would invite a second tap.
- Tests: `tests/test_host_control_guard.py`; no test runs a host command.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.40.0] - 2026-10-17

### One destructive host operation at a time

- Audit finding: panel update buttons had no mutual exclusion, so a second press started a second host operation: two `apt-get upgrade` runs contend on the dpkg lock, and two `self_update.py --apply` units deploy the same release at once.
- New `HostOperationGuard` (`bridge/services/host_operations.py`) with a process-wide `HOST_OPERATIONS`. `update_now()` validates the kind then hands the whole operation to the guard; a concurrent request is refused immediately with an Arabic message naming the running operation instead of being queued.
- Releases in `finally`, so a failed or timed-out update cannot block the next attempt. Tests in `tests/test_host_operation_guard.py`; no test runs a host command.
- Follow-up completed in 3.41.0: `HostController.reboot`, `restart_service` and `cleanup_now` now consult the same guard, and a refused request runs no host command.

### Upgrade notes

- No dependency changes, no migrations. Source release only.

## [3.39.0] - 2026-10-17

### Progress persistence no longer writes once per event

- Every summarized event called `queue.update_activity`: an UPDATE plus a WAL commit on the shared SQLite connection, under the queue lock. A tool-heavy task emits one event per tool transition, so a deep-research run spent dozens of commits and delayed the next claim behind the same lock.
- `LiveProgressReporter` coalesces those writes behind a 5s window (`PERSIST_INTERVAL_SECONDS`); the in-memory store stays authoritative and keeps every entry.
- Nothing owner-visible changes: `/progress` and `/trace` render from memory whenever a live task exists; the persisted trail is the post-restart and history view only.
- Boundaries stay correct: `start`, `finish`, `finalize_text` and the end of the event stream force a flush, including on cancellation, and a flush carries every coalesced entry.
- Tests: `tests/test_progress_persistence.py` (20 events to one write, window expiry, dedupe preserved, forced flushes, stream-end flush, cancelled stream).

### Upgrade notes

- No dependency changes, no migrations. Worst case after a crash mid-task: the last seconds of live activity are missing from the persisted trail.
- Source release only.

## [3.38.0] - 2026-10-17

### PDF text reaches the model

v3.37.0 left the media processors out of the task path because they shell out synchronously: one PDF could freeze the whole event loop, every Telegram update and queued task, for up to two minutes. This cycle fixes the blocking instead of avoiding the feature.

- New `MediaAnalysisService` runs the blocking pipeline in a worker thread (`asyncio.to_thread`), caps the wait with `asyncio.wait_for` (45s), and gives the tool its own shorter subprocess timeout (30s) so a thread that timed out on our side still terminates instead of leaking. PDF text chunks become one bounded, document-ordered excerpt reusing the in-process caps and the shared `format_block()` rules.
- Every failure yields **no** representation rather than a guess: tool missing, non-zero exit, wedged tool, scanned PDF with no text layer. A PDF is never a direct-model part, so extraction can only add context and displace nothing.
- `PdfProcessor` gained an injectable `timeout` (default 120, unchanged for existing callers).
- `TaskExecutionService` awaits the media step after the in-process one, merges both representation sets for the part-exclusion decision, and audits the combined strategies in one event.
- Tests: `tests/test_media_analysis_service.py` (detection, document order, selection and caps, scanned PDF, non-PDF never reaching the tool, tool failure, `TimeoutError`, and a wedged-tool deadline test) plus two wiring tests for the merged prompt/audit path and media-failure degradation. No test executes a host tool.

### Upgrade notes

- No dependency changes, no migrations. Uses `pdftotext` only when already installed; the bridge still never installs anything, and without that tool behaviour is exactly as before this cycle.
- Source release only.

## [3.37.0] - 2026-10-17

### Attachment intelligence, actually wired (the deferred v2 backlog item)

`AttachmentIntelligenceRouter`, the media pipeline, and the PDF/video/audio/ZIP processors existed since the v2 baseline with unit tests — and nothing in the running bot ever called them. Every PDF, ZIP or hidden text attachment reached the agent as a bare local path, so the quality of the answer depended on the agent happening to run the right host tool.

- New `AttachmentContextService` derives a bounded, in-process representation with **no new dependency and no subprocess**: query-relevant text excerpts (2 000-char chunks, query selection, per-file cap 8 000 chars, per-task cap 24 000 chars, at most 5 files and 256 KiB read per file) and validated ZIP manifests (entry count, uncompressed total, capped at 200 entries) built on the existing archive safety limits.
- New `AttachmentIntelligenceRouter.select_ordered_chunks()`: same relevance selection as `select_text_chunks`, but returned in **document order**. A score-ranked excerpt prints "part 7, part 3, part 9", which lets the model draw the wrong conclusion about what precedes what. `select_text_chunks` keeps its existing contract and test.
- `TaskExecutionService` injects the service (default constructed, no I/O) and appends the derived block to the prompt, labelled as untrusted data. A represented attachment is no longer uploaded as a raw part — the excerpt replaces it while its local path stays in the attachment note, so the agent can still open the original. Any failure degrades silently to the previous path-only behaviour.
- Audit event `attachment_context_derived` records strategies, count and truncation (no content).
- Deliberately not used: the media pipeline's `PdfProcessor`/`VideoProcessor`/`AudioProcessor`, which call `subprocess.run(..., timeout=120)` **synchronously**. Running those in the event loop would freeze the whole bot for up to two minutes per attachment. They stay out of the task path until they are made async with a deadline.
- Tests: `tests/test_attachment_context_service.py` (classification, bounded reads, chunk budget including the marker, document order, archive caps, unsafe/corrupt archives skipped rather than guessed, per-task cap) and `tests/test_attachment_context_wiring.py` (inlining, part exclusion for represented files, visible files untouched, failure degradation, real-service ZIP end to end).

### Upgrade notes

- No dependency changes, no migrations, no new host tools. Extracted content is bounded (≤ 24 000 chars per task) and arrives marked as untrusted data.
- PDFs, images, audio and video are unchanged: they still reach the agent as validated local paths, because this cycle adds no external parser.
- Source release only — host upgrade follows the safe-update path.

## [3.36.0] - 2026-10-17

### `/metrics` — live operational metrics (owner-only)

The v2 baseline shipped `MetricsRegistry`/`PrometheusEndpoint` and unit tests but nothing ever populated them: dead observability. This wires real numbers into the authenticated Telegram surface instead of opening a new network port.

- `TaskQueueStore.queue_counts()`: one index-covered `GROUP BY status`, mapped through the real status vocabulary (`queued`, `scheduled`, `leased`, `running`, `retrying`, `completed`, `failed`, `dead_letter`, `cancelled`).
- `BridgeMetricsService`: snapshots on demand — **no background sampler**, so an idle bridge spends nothing. Counters derive from SQLite and the audit log (`task_finished` durations, `task_deferred` categories), so they survive restarts instead of resetting like in-memory gauges. Host memory/CPU/free-disk reuse the existing portable `sample_resources`. A failing store or audit read degrades to zeros instead of failing the command.
- `audit_log.read_recent()`: bounded tail reader (last 256 KiB) that skips partial/broken lines — one seek plus one small read instead of parsing a 10MB log. The tail bound is documented in the service docstring: outcome figures cover at most what the tail still holds, while queue gauges stay exact.
- `window_argument()` reads `context.args`, never `update.message.text`: the control panel dispatches the same handlers with the callback's own message, where parsing text would read an unrelated word from a caption.
- `/metrics [hours]` (clamped 1–720, default 24) registered in the command menu, `/help`, the advanced/server section of the control panel, and the legacy dispatch allowlist.
- Tests: `tests/test_metrics_command.py` (tail reader, window math, status mapping, store-failure degradation, argument clamping, panel dispatch, rendering with and without history/resources); the control-UI command-coverage contract now includes `metrics`.

### Upgrade notes

- No dependency changes, no migrations. New read-only command; no new listening port, so the host attack surface is unchanged.
- Source release only — host upgrade follows the safe-update path.

## [3.35.0] - 2026-10-17

### Flag reads hardened (availability + fewer disk reads)

- `runtime_flags.enabled()` stays strict on purpose: startup gates like `OPENCODE_FULL_ACCESS` must fail closed loudly rather than silently widen privileges. Task paths now use the new `enabled_lenient()`, which degrades a malformed value to the documented default and logs one warning per flag name. Previously a single typo (e.g. `AGENT_LIVE_OUTPUT=ture`) raised `ValueError` inside `LiveProgressReporter.__init__`, outside the task's error handling, and failed every task with a configuration-shaped traceback.
- `LiveProgressReporter` now reads its flags once per task (`_live_enabled`, `_stream_drafts`) instead of re-reading on every preview tick. With the flag absent from the environment, `enabled()` falls back to parsing `.env` from disk, so the per-tick read was a full file read every ~1.5s for the lifetime of each active task.
- README documents `TELEGRAM_STREAM_DRAFTS` alongside `AGENT_LIVE_OUTPUT` and the fail-soft rule.
- New `tests/test_runtime_flags.py`: strict raise, lenient degradation with a single warning, env-over-dotenv precedence, defaults, and reporter caching (no re-read during `_publish_live`).

### Upgrade notes

- No dependency changes, no migrations. Default behaviour unchanged: both flags default off.
- Note: `.env.example` could not be edited from the build workspace (harness blocks writes to `.env*` paths). Add the commented `TELEGRAM_STREAM_DRAFTS=0` block next to `AGENT_LIVE_OUTPUT=0` when deploying.
- Source release only — host upgrade follows the safe-update path.

## [3.34.0] - 2026-10-17

### CI lint pin refreshed

- Quality gate now installs `ruff==0.17.1` (released 2026-10-15; patch release, no new rules — the only noted default change is the implicit target-version bump, which `ruff.toml` overrides explicitly with `py312`). Sole version pin in CI; historical notes untouched.

### Upgrade notes

- CI-only change. No runtime, dependency, or migration impact.
- Source release only — host upgrade follows the safe-update path.

## [3.33.0] - 2026-10-17

### Quota hot-loop fix

- A 429 whose `Retry-After` lands exactly on the noon UTC reset is the Zen free bucket even when the model id is missing or renamed. Previously only `-free`-suffixed models took the wait-until-noon path; unknown models hot-retried every 60s against an exhausted daily bucket. The reset match alone now classifies `zen_free_quota` (markers still require the free suffix; non-matching 429s keep the generic `quota` path).
- Covered by noon-match tests (None/empty/renamed model → noon wait; non-matching → generic quota with pinned clock).

### Upgrade notes

- No dependency changes, no migrations. Fewer wasted retries under exhaustion.
- Source release only — host upgrade follows the safe-update path.

## [3.32.0] - 2026-10-17

### Live-preview redaction hardened

- `redact_output` covered only field-style secrets and Bearer tokens. An assistant echoing tool output could still leak Telegram bot tokens, GitHub tokens, or `sk/pk/rk` vendor keys into previews (and now drafts). Added the three token-shape patterns mirrored from the host-control guard, keeping the uniform `[REDACTED]` marker.
- Covered by `TokenShapeRedactionTests` (masked shapes + ordinary-text negative including `risk-free`/`disk space`/`12:00` near-misses).

### Upgrade notes

- No dependency changes, no migrations. Preview/draft text only.
- Source release only — host upgrade follows the safe-update path.

## [3.31.0] - 2026-10-17

### Experimental animated streaming drafts

- Verified against official PTB v22.8 docs that `Bot.send_message_draft` exists (added 22.6, all bots since 22.7): new `send_stream_draft()` helper posts the live preview as an animated draft and reports acceptance, declining safely on missing methods, zero ids, over-length text (capped 4096), and any API failure including non-private chats.
- `_publish_live` uses it only behind opt-in `TELEGRAM_STREAM_DRAFTS=1` (same flag mechanism as `AGENT_LIVE_OUTPUT`); every failure path falls back to the existing status-message edit, so default behavior is byte-identical.
- Covered by helper tests (accept/decline/cap) and reporter branch tests (draft-skips-edit, failure-falls-back, flag-off-keeps-edit). Documented as experimental in the direct/daily guide.

### Upgrade notes

- No dependency changes, no migrations. Opt-in flag, default off.
- Source release only — host upgrade follows the safe-update path.

## [3.30.0] - 2026-10-17

### Documentation completeness

- `/help` now lists the `/reset` alias alongside `/start`/`/new` (it was a working but undocumented alias). Pipe-argument grammar reviewed against the domain `split_pipe_args` — the optional-limit form in `parse_history_args` is intentionally more lenient and stays separate.

### Upgrade notes

- Docs only. No dependency changes, no migrations.
- Source release only — host upgrade follows the safe-update path.

## [3.29.0] - 2026-10-17

### Documentation correctness

- `OPENCODE_DIRECT_DAILY_AR.md` prescribed `pytest`; the repo is unittest-only (no pytest in requirements or CI). Now points at the real commands (`unittest discover …` / `scripts/verify.sh`).

### Scoped, not started

- Attachment-intelligence representations (chunk indexing, PDF decomposition, transcripts) assessed: the router classifies only — builders and agent-protocol changes are a multi-cycle feature, deliberately left unwired rather than half-integrated.

### Upgrade notes

- Docs only. No dependency changes, no migrations.
- Source release only — host upgrade follows the safe-update path.

## [3.28.0] - 2026-10-17

### Large-file guidance gap closed

- Button downloads that exceed direct sending (oversize fetch or more parts than the ~6GB split ceiling allows) no longer end in a generic error: the bot now points at `/mirror big <n>` to split-upload via mirrors. Import is function-local like the file's existing pattern.

### Upgrade notes

- No dependency changes, no migrations. One error message improved.
- Source release only — host upgrade follows the safe-update path.

## [3.27.0] - 2026-10-17

### Quota transparency (legitimate observability)

- `/limits` now ends with the daily free-quota reset countdown (12:00 UTC) and, while a recorded exhaustion pause is in force, the automatic resume time. Read-only lines from the existing pause record and reset clock — enforcement untouched, no rotation added, no policy changed.
- Covered by `test_quota_status` (countdown, active pause, expired pause hidden).

### Upgrade notes

- No dependency changes, no migrations. `/limits` gains up to two lines.
- Source release only — host upgrade follows the safe-update path.

## [3.26.0] - 2026-10-17

### Telegram transport tuned with verified parameter names

- Resolved the cycle-5 deferral against the official PTB v22.8 `HTTPXRequest` docs: `connection_pool_size` confirmed (applies to `max_connections` since 22.4). Both request objects now cap at 32 pooled connections instead of 256 each — ample for a private bot, lighter on a 1GB host.
- Same edit fixes a real large-file hazard on the same objects: the default 20s `media_write_timeout` could abort 49MB part uploads on slow uplinks, breaking the 4GB+ delivery path. Raised to 300s.

### Upgrade notes

- No dependency changes, no migrations. Transport-only change.
- Source release only — host upgrade follows the safe-update path.

## [3.25.0] - 2026-10-17

### Hygiene sweep across all cycle files

- Systematic unused-import review of every file touched since 3.6.0 (`manage.py`, `execution.py`, `mirrors.py`, `shell_exec.py`, `opencode_client.py`, `model_manager.py`, `host_credentials.py`): exactly one redundancy found — a shadowed `COMPLETION_SENTINEL` in the lazy import now that it lives at module top of `task_execution_service.py`. Removed.
- Re-verified boundary invariants hold: services import no `telegram`/`bot` roots (rich-delivery use is `bridge.*`-namespaced lazy), command adapters contain no SQL, handlers stay single-await delegates.

### Upgrade notes

- No dependency changes, no migrations, no behavior change.
- Source release only — host upgrade follows the safe-update path.

## [3.24.0] - 2026-10-17

### Dead compatibility shim removed

- Deleted `V3TaskService` from `v3_plugin.py` plus its `core.TaskService` rebound on install: no production path ever read it back, and `run_v3` documents that the stabilized `bot.TaskService` must stay. Also removed the four imports/constants used only by the shim. `run_v3` comment updated to match. Verified against architecture-boundary tests (handler thinness, import roots, no-SQL adapters) — all hold.

### Upgrade notes

- No dependency changes, no migrations, no behavior change (dead code only).
- Source release only — host upgrade follows the safe-update path.

## [3.23.0] - 2026-10-17

### Coverage contract

- Verified all ~50 `BotCommand`s resolve to a handler (including `reset`/`stop` aliases and the shared research-mode handler) and the full legacy-dispatch allowlist maps to `cmd_*` handlers.
- New `test_command_coverage` locks all three registries (commands, dispatch, control UI) so future additions cannot silently drop a surface.

### Upgrade notes

- Tests only. No dependency changes, no migrations, no behavior change.
- Source release only — host upgrade follows the safe-update path.

## [3.22.0] - 2026-10-17

### Documentation drift fixed

- `OPENCODE_DIRECT_DAILY_AR.md` now documents `/taskshow`, `/dailies` management, automatic multi-message delivery with source buttons, the `answer-full.md` overflow file, and the done-phrase completion fallback — all shipped behavior that the doc predated.

### Upgrade notes

- Docs only. No dependency changes, no migrations.
- Source release only — host upgrade follows the safe-update path.

## [3.21.0] - 2026-10-17

### Agent prompt catches up with delivery

- `prompts/telegram-operator-ar.md` gains a "تسليم تيليغرام الغني" section: don't split manually, embed real `https` image URLs (delivered as photos), and link every key item's original source (open-page buttons + pinned large preview). Bans invented image/source links, consistent with the existing no-fabrication rule. Behavior-only prompt change; no code touched.

### Upgrade notes

- No dependency changes, no migrations. Digests get more visual without any code deploy risk.
- Source release only — host upgrade follows the safe-update path.

## [3.20.0] - 2026-10-17

### Visibility gap closed

- `/limits` never showed the download caps or the pending-updates flag, so operators could not verify the 8GB/40GB/50MB/TTL settings from Telegram. `limits_dict()` now includes all five values with Arabic labels in `render_limits`. Covered by `test_limits_visibility`.

### Upgrade notes

- No dependency changes, no migrations. `/limits` output gains five lines.
- Source release only — host upgrade follows the safe-update path.

## [3.19.0] - 2026-10-17

### Truth-in-delivery fix

- The overflow note claimed the remainder was "saved in the task file" while no such file existed for answers beyond 9 follow-up chunks. `TaskExecutionService` now materializes `answer-full.md` (0600) into the task output directory before delivery whenever follow-ups cannot cover the text, so the note — now naming the file — is true. Covered by `test_overflow_delivery` (saved + sent / short-answer negative).

### Upgrade notes

- No dependency changes, no migrations. Only over-long answers gain an extra attached file.
- Source release only — host upgrade follows the safe-update path.

## [3.18.0] - 2026-10-17

### Rich topics: pinned large link previews

- Verified against the official PTB v22.8 docs that `LinkPreviewOptions` (available since 20.8) is supported by the pinned dependency — resolving the cycle-3 deferral with evidence instead of guessing.
- `rich_delivery.send_long_text` now pins the first source URL of a digest with `prefer_large_media` on the first chunk; follow-ups keep previews enabled; the restricted-chat retry and the overflow note explicitly disable previews. The legacy `disable_web_page_preview` flag is gone from this path (legacy status/progress edits untouched).
- Covered by `PreviewOptionsTests` + `SendLongTextTests` (pin attributes, no legacy flag, disabled retry).

### Upgrade notes

- No dependency changes, no migrations. Previews are now richer on topic digests; all other messages byte-identical.
- Source release only — host upgrade follows the safe-update path.

## [3.17.0] - 2026-10-17

### Correctness fix: portable `.env` credential writes

- `HostCredentialManager._set_env` wrote secret values raw. Values with spaces broke shell `source` (used by `start.sh`) even though the bridge parser tolerated them. New `quote_env_value()` quotes outside a portable-safe set with backslash escapes — readable by the bridge parser, shell sourcing, and systemd `EnvironmentFile` alike. Plain tokens stay byte-identical (existing `GITHUB_TOKEN=validSecret123` assertion unaffected).
- Covered by `test_env_quoting` (bare/quoted/escaped cases + spaced-secret activation roundtrip).

### Upgrade notes

- No dependency changes, no migrations. Existing `.env` files untouched; quoting applies to future writes only.
- Source release only — host upgrade follows the safe-update path.

## [3.16.0] - 2026-10-17

### Consistency fix: explicit guard policy for the owner shell

- `/exec` bypassed the build/hardline request guards silently while `/direct` enforces them. The bypass is intentional (owner shell = SSH equivalent, as originally requested) but is now explicit: `policy_advisory()` in `shell_exec.py` attaches a ⚠️/ℹ️ banner for destructive matches without ever refusing, and the audit entry records whether a banner was shown. `SECURITY.md` documents the bypass-by-design.
- Covered by `PolicyAdvisoryTests` (safe/build/hardline cases).

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.15.0] - 2026-10-17

### Customization

- `AuditLogger` rotation cap is now a constructor option (`max_bytes`, default 10MB, floor 1KB): all four production call sites keep the default; covered by `test_audit_rotation` (default cap, rotation to `.1`, no-rotation path).

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.14.0] - 2026-10-17

### Efficiency

- `/model` status view (`AgentService.model_overview`) reuses the timestamped catalog cache via new public `cached_providers()` / `cache_providers()` instead of fetching on every open; UI-triggered fetches refresh the same task cache. Covered by a fetch-free roundtrip test.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.13.0] - 2026-10-17

### Efficiency

- `ModelManager` now reuses a timestamped provider catalog for up to one sync interval: per-task model selection (and the per-image media check) no longer performs 1–2 extra loopback catalog fetches per task. Stale entries refetch automatically; background reconcile/scout stamping flows through the same helper. Covered by `CatalogCacheTests`.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.12.0] - 2026-10-17

### Security documentation

- `SECURITY.md` gains a control-surface threat model covering the direct shell (owner-only, private chat, audited, bounded, service-user privileges), fixed-argv host controls, untrusted-attachment handling, SSRF-guarded downloads/mirrors, and redacted rotating audit logs. No code changes; documents guarantees the code already enforces.

### Supply-chain note

- npm `@opencode-ai/plugin` pin re-checked (lockfile integrity intact; Dependabot covers weekly). No conclusive newer stable found; no churn.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.11.0] - 2026-10-17

### Testability

- `/schedhistory` pipe/limit grammar extracted to pure `parse_history_args()` in `commands/schedules.py` (identical behavior, adapter now 3 lines); covered with `test_schedule_history_args` alongside `ManageCommands.describe_prompt_files`.
- Hygiene sweep of recent code: no bare `except`, no other dead helpers; progress entries, audit logs, and history limits all bounded and covered.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.10.0] - 2026-10-17

### Efficiency

- yt-dlp supervisor poll relaxed from 0.5s to 2s (`download_service.py`): the per-tick `rglob` + `stat` walk over fragment files was pure wakeup overhead — quota overshoot is still enforced on completion, so slower polling trades wakeups, never safety.
- `opencode_client.py`: hoisted a function-level `import re` to module top.

### Supply-chain re-verification

- Transitive lock re-checked against PyPI (Oct 2026): `anyio==4.15.1`, `httpcore==1.0.9`, `idna==3.20`, `certifi==2026.7.22` all latest; `OpenCodeClient` already reuses one bounded `AsyncClient` (20/10 pool); Dependabot covers pip/npm/actions weekly. No changes required.

### Deliberately not changed

- PTB `HTTPXRequest` pool sizing left at library defaults: the exact parameter name differs across PTB minor versions and cannot be verified from this workspace — a wrong guess raises at startup and kills the bot.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.9.0] - 2026-10-17

### Stability: regression locks for recent features

- New suites: `test_prompt_files`, `test_rich_delivery`, `test_shell_exec`, `test_daily_completion` — the prompt-file, rich-topics, direct-shell, and daily-completion paths from recent cycles are now CI-guarded.
- Removed dead helpers `looks_like_execution_request` / `looks_like_prompt_name` (+ their regexes) from `prompt_files.py`: defined but never called anywhere.
- Fixed a latent lint failure: `bridge/services/shell_exec.py` imported unused `run_fixed` (ruff F401 breaks the quality gate).

### Correctness fix from test cross-check

- Daily completion rule tightened without losing robustness: a same-line tail after the sentinel only completes when empty, ≤8 chars, or itself a done-phrase. The pre-existing guard (`'Example: [[…]] is the marker.'` → not complete) passes again alongside the new Arabic/English fallbacks.

### Docs

- `/help` documents `/schedhistory الاسم [| العدد]`; `DEPLOYMENT_AR.md` documents opt-in `TELEGRAM_DROP_PENDING_UPDATES`.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.8.0] - 2026-10-17

### Memory bounds

- Live SSE preview backlog per task reduced from 50k/45k chars to a 20k cap keeping the last 15k. The user-visible preview window (2.9k chars) is unchanged; only the throttled-backlog worst case shrinks.

### Control and customization

- `/schedhistory` accepts an optional row limit: `/schedhistory الاسم | 5` (default 20, clamped to 100 like the repository layer). Names containing `|` without a numeric tail keep resolving verbatim.

### Deliberately not changed

- `LinkPreviewOptions` migration deferred: `disable_web_page_preview` is used consistently across ~15 send sites and the installed PTB behavior cannot be verified from this workspace; a blind migration risks breaking every delivery path at once.
- Schedule-history rendering already truncates to the message cap; the new limit only reduces rows fetched.

### Upgrade notes

- No dependency changes, no migrations, defaults preserve prior behavior.
- Source release only — host upgrade follows the safe-update path.

## [3.7.0] - 2026-10-17

### Python 3.14 compatibility (real findings, not churn)

- `session_store.py` no longer calls the deprecated `datetime.utcnow()` (removed in 3.16, warning-as-error on the newest CI job); session timestamps are now timezone-aware UTC. Stored ISO strings parse identically via `fromisoformat`, so existing databases need no rewrite.
- Full-tree audit for other 3.14 removals (`asyncio.iscoroutinefunction`, `utcfromtimestamp`, `get_event_loop`, …): the remaining `fromtimestamp` calls already pass explicit `tz` and are compliant.

### Database efficiency

- New migration 9 adds a covering index `idx_agent_tasks_owner_status_updated (owner_id, status, updated_at DESC, id DESC)` for the clamped `list_active`/`list_recent`/`list_failed` queries. Applies automatically with checksum tracking; `IF NOT EXISTS` makes it idempotent.
- Confirmed the watchdog and maintenance audit logs already rotate through the shared `AuditLogger` cap from 3.6.0 — no second implementation needed.

### Deliberately not changed

- Polling intervals (`task_poll_seconds`, `opencode_client` 5s session poll, watchdog cadence) stay as-is: they are `asyncio.sleep` waits (~zero CPU) woken on demand by `wake_tasks()`, so lengthening them would only add user-visible latency for no measurable saving.
- No dependency changes: pins re-verified latest stable.

### Upgrade notes

- Migration 9 runs automatically on next start; downgrade needs no action (extra index is harmless).
- Source release only — host upgrade follows the safe-update path.

## [3.6.0] - 2026-10-17

### Resource efficiency (1GB host)

- SQLite read path now sets bounded `cache_size`, in-memory `temp_store`, a conservative 64MB `mmap_size`, and a capped WAL file (`journal_size_limit`) alongside the existing WAL/`NORMAL`/`busy_timeout` guards.
- Task list queries (`list_active`, `list_recent`) clamp caller limits to a maximum of 100 rows; `list_failed` and schedule history already did.
- The JSONL audit log rotates at 10MB to a single `.1` generation instead of growing without bound on unattended hosts.

### Control and customization

- New opt-in `TELEGRAM_DROP_PENDING_UPDATES` (default `0`): when enabled, the bot skips the backlog accumulated while offline instead of replaying it after downtime.
- Python compatibility matrix now covers 3.12, 3.13 and 3.14 (the development build already ran 3.14).

### Dependency audit (October 2026)

- Verified latest stable pins with no change required: `python-telegram-bot==22.8`, `httpx==0.28.1`, `aiosqlite==0.22.1`, `ruff==0.17.0` (CI gate).

### Upgrade notes

- No dependency changes and no new deploy units; all new behavior is backwards compatible with existing defaults.
- This is a GitHub source release, **not** confirmation that the Telegram service was upgraded or smoke-tested on its production host.

## [3.5.0] - 2026-10-14

### Owner host controls

- New owner-private Telegram service-control panel (`svcctl`): restart OpenCode, the Bridge, or the whole stack, each behind an explicit confirmation.
- Live process and disk inspection (`procs`, `disk`): top memory consumers by name only, largest managed folders and files; command lines are never shown.
- One-tap expired-file cleanup reuses the same retention guards as the midnight run; the previous-day rule stays exclusive to the scheduled job.
- Optional server reboot (`power`) through a dedicated single-command sudoers rule, installed only with `OPENCODE_ENABLE_POWER_CONTROL=1`; when unset, the current state is left untouched.
- All new panels join the existing `ADMIN_SCREENS` gate: private owner chat only, with one shared check for rendering and handling.

### Detached, self-reporting updates

- Bridge updates now run in a transient systemd unit outside the bot's cgroup, so the restart they perform cannot kill the updater mid-flight; the next bot process announces the outcome to the owner automatically.
- Interrupted updates are reconciled on start-up (`healthy` vs `quarantined`) instead of retried blindly; one killed update can never block later releases.
- The update source is configurable and strictly validated (`OPENCODE_UPDATE_REPOSITORY`), with a clamped deploy timeout (`OPENCODE_UPDATE_DEPLOY_TIMEOUT`, 60-1800s, default 420s).

### Deployment hardening

- `deploy.sh` and artifact activation no longer depend on a sudoers rule for the user-writable installer; root-owned assets refresh through the daily root run, with a `root-assets-stale` flag in between.
- `deploy.sh` is invoked through `bash` explicitly, so checkouts that lost the executable bit (zip/Windows extraction) no longer quarantine a good release.
- systemd units gain restart backoff (3-5s to 60s) plus memory and task ceilings sized from observed idle footprints.

### Fixes

- The new detached-update tests create their `runtime/` fixture `0o700`: the default mkdir mode inherits permissive umasks (002/022) on CI and servers, which tripped the v3.3.0 journal hardening with a false `error` instead of `updated`. The production check is untouched.

### Upgrade notes

- No dependency changes, no new deploy units, and the full-access permission profile is unchanged.
- To enable Telegram-initiated reboot (off by default): `OPENCODE_ENABLE_POWER_CONTROL=1 sudo -E bash maintenance/install-root-assets.sh`.
- This is a GitHub source release, **not** confirmation that the Telegram service was upgraded or smoke-tested on its production host.

## [3.4.0] - 2026-10-13

### Telegram conversation quality

- Deliver concise, natural user-facing answers while preserving one editable Telegram status message per request.
- Hide internal prompts, tool results, reasoning, and synthetic SSE content from live previews; verify assistant authorship before any optional text preview.
- Prefer focused verification for short exchange-rate questions while retaining explicitly requested deep research.
- Restore final-answer delivery with a guarded fallback if Telegram refuses to edit the existing status message.
- Add targeted regression coverage for message delivery, streaming privacy, currency intent, and failed edits.

### Verified release publishing

- Publish version changes only after the main-branch development build and the independent Python 3.12/3.13 compatibility suite succeed.
- Create a version tag for the verified, current main revision, then explicitly dispatch the existing tag-triggered build and GitHub Release publication workflow.
- Keep releases fail-closed on stale revisions, non-increasing versions, conflicting tags, or failed checks. No host deployment is implied.

### Upgrade notes

- The recommended `AGENT_LIVE_OUTPUT=0` default is used unless the existing private runtime `.env` overrides it. Set it to `0` explicitly for installations that previously enabled raw SSE streaming.
- This is a GitHub source release, **not** confirmation that the Telegram service was upgraded or smoke-tested on its production host.

## [3.3.0] - 2026-10-13

### Consolidated release since v3.2.0

- Hardened verified-release coordination with SQLite WAL journal, exclusive activation, quarantine after failed deployments, and restart-safe operation history.
- Added owner-controlled Bridge update modes (`off`, `notify`, `auto-safe`, `maintenance-window`) and an `emergency-stop` that blocks Bridge/agent/OS updates and scheduled mutating maintenance. Unset update controls are off by default.
- Added explicit `TELEGRAM_OWNER_ID` support. If multiple Telegram users are allowed, privileged host controls now require an explicitly allowlisted owner; single-user installations keep their existing owner.
- Restricted unattended Ubuntu updates to explicit owner opt-in, avoiding automatic destructive `apt autoremove --purge`.
- Added protected SQLite snapshot checksums, offline verification of encrypted age archives, bounded-memory backup creation, safe backup-retention accounting, and a synthetic real-age recovery smoke test (optional when age tools are absent).
- Added a read-only redacted host-inventory CLI and updated the T01–T18 operational evidence register.
- Enforced fail-closed managed-download index validation, bounded public-IP TCP connections against DNS rebinding in direct HTTP downloads, 24-hour maximum local retention, UTC-midnight expiry guards and crash-released interprocess locks.
- Added an optional systemd user timer for UTC-midnight cleanup; this timer is **not enabled automatically**, pending staging and end-to-end concurrency validation.
- Expanded security, crash-recovery, backup and download regression coverage across Python 3.12, 3.13 and 3.14 in GitHub Actions.

### Upgrade and testing notes

- For multi-user Telegram installations, privately configure `TELEGRAM_OWNER_ID` as one of the IDs in `TELEGRAM_ALLOWED_USERS` before trying owner-only server controls.
- Automatic Bridge and Ubuntu upgrades stay disabled without explicit opt-in; inspect the update mode before changing it. Emergency stop does not pause regular user tasks.
- A completed GitHub Actions build does **not** certify production operation. Before enabling automatic upgrades or the cache timer, validate offline age-key restoration, service restart, rollback, network isolation and a staging soak test.
- This is a source release and does not automatically deploy the bot to the host. Unfinished T01–T18 staging acceptance requirements remain tracked in `docs/autonomous-operations-evidence-2026-10-13-ar.md`.

## [3.2.0] - 2026-10-12

- Telegram panels for host credentials, encrypted/private SQLite backups, free anonymous mirrors and host resource inspection.
- Separate OpenCode and OS update-now actions with progress, and independent automatic OS update switch.
- Owner-only private credential operations, 0600 storage, selected host identities and no raw-token disclosures.
- Safe encrypted backups with age recipient setup, optional post-release cleanup, and existing release backup inventory.
- Return temporary download retention to 24 hours, with expiration cleanup daily at 00:00 UTC.

## [3.1.0] - 2026-10-12

### Telegram task controls
- Delete queued or finished tasks from owner-scoped Telegram buttons after confirmation; running tasks must be stopped first.
- Promote or demote queued work; the SQLite leasing scheduler honors priority while preserving blocked deferred tasks.

### Managed media
- yt-dlp video and audio extractor for supported public services, plus the existing guarded HTTP downloads.
- Automatic multipart file re-delivery under the normal Telegram Bot API upload limit.
- Managed storage capped at 20 GiB, default 72-hour expiration, and optional yt-dlp/aria2/ffmpeg tools provisioned during initial host setup.

### Memory and local scheduling
- Automatic ZRAM sizing scaled with available RAM, configurable from Telegram.
- Country/IANA timezone selection for the user's scheduled local time, converted to UTC without changing the server clock.
- Regression tests covering IANA daylight saving, queue priorities and multipart uploads.

### Operational notes
- Site restrictions, DRM and authentication can prevent yt-dlp from downloading some media; no universal-site guarantee.
- The bot does not compile application projects on the server. All application verification stays on GitHub Actions.

## [3.0.0] - 2026-10-12

### Telegram operation
- Bilingual Arabic/English Telegram keyboards and owner-only panels for agents, models, reasoning, long-running jobs, scheduling, files, server controls and diagnostics.
- Provider-specific API key inventory: add, activate, remove, and rotate on credential-scoped errors.
- Strict one-task default and two-task ceiling; durable continuation after the 12:00 UTC quota reset.

### Deployment and resources
- Stable GitHub Release updater gated by successful tagged CI, matching commit and semantic version, with rollback on deployment failure.
- First-install development CLI provisioner without local application builds; restricted GitHub-only agent shell permissions.
- Adaptive disk swap, configurable ZRAM, recurring memory policy timer, and Telegram controls.
- Align service working directory and configuration with the deployment checkout so version updates activate the correct code and preserve persistent state.

### Upgrade notes
- Release upgrades run from the /home/ubuntu/opencode-bridge checkout. The deploy script refreshes systemd units.
- Python environments must already be provisioned; builds and dependency compilation stay on GitHub Actions.
- Configure dedicated GitHub access and scoped sudo permissions for unattended deployment.
- Never send API keys into public Telegram chats.

## [Unreleased]

### OpenCode Zen free quota recovery and worker admission

- A confirmed OpenCode Zen free-model daily limit now opens a durable provider-wide pause until the next 00:00 UTC reset. Queued work resumes automatically after the pause, including after a service restart, without repeatedly calling the exhausted model.
- New Zen free-model tasks check the saved pause before sending a model request; unrelated paid or non-Zen models remain available.
- Task admission is capped at three active leases globally in SQLite and three workers per process. The resource-aware controller refreshes host measurements before each claim, reduces concurrency for memory/CPU/disk pressure, and fails closed if sampling fails.
- The production resource policy already limits hosts with less than 1.5 GiB RAM to one worker; polling remains bounded to 60 seconds so due work is picked up promptly after UTC reset.

## [2.0.0-rc.6] - 2026-09-30

### Managed downloads

- Added `/download <url>`: fetches one http or https link into a managed root, keeps it for a retention window, and sends the file back over Telegram when it fits the direct-send limit. Larger files are recorded in the panel instead.
- Added a downloads panel under `/menu` listing stored files with send and delete buttons, and showing the real limits, free space, and whether video extraction is available on the host.
- Added `/deletefile <id>`.
- Retention is enforced every 15 minutes by a loop that runs once at startup and then on the interval; the interval is configurable and the loop cannot take the bridge down.

### Untrusted input handling

- Downloads are refused before a single byte is fetched when the scheme is not http or https, or when the host is loopback, private, link-local, reserved, multicast, `localhost` or `.local`. The cloud metadata address and the host's own private address are both covered.
- Every address a hostname resolves to is checked, not just the literal, so a public name pointing inward is still refused.
- The redirect chain is validated after the response arrives, so a redirect cannot walk the transfer into the internal network.
- The test-only `allow_private_hosts` escape hatch defaults to False, and a dedicated test asserts that production gets the strict policy and that the escape hatch does not relax the scheme rule.

### Storage

- Files are written to a `.part` path first and only moved into place once the transfer completes, so a failure, a size cap, or a full budget never leaves a partial file or a stale index entry.
- Per-file and total budgets bound the disk regardless of how many files are requested.
- The filename comes from the server response, is Unicode-normalized and sanitized, and the result is asserted to stay inside the managed root.
- Each owner gets their own directory, the index is written atomically at 0600, and list, get, and delete all filter by owner.
- A corrupt index is discarded instead of breaking every read.

### Dependencies and architecture

- No new runtime dependency: downloads use the already-pinned httpx, and video extraction is an optional host tool rather than a package, because the exact-pin supply-chain policy allows only three direct runtime dependencies.
- The download logic lives in `bridge/telegram/commands/downloads.py` so the `bot.py` handlers stay single-await delegates, as the architecture boundary test requires.

### Verification

- Added 18 end-to-end tests against a real HTTP server on a real socket: a real download with byte-for-byte verification, index survival across service instances, owner isolation, eight SSRF shapes, scheme rejection, size and budget limits, partial-file cleanup, filename sanitization, expiry cleanup, cross-owner delete refusal, corrupt index recovery, and host capability reporting.
- Total suite: 498 tests.
## [2.0.0-rc.5] - 2026-09-29

### Fixes

- Fixed the root cause of `TELEGRAM_API_MODE=local` never working: `local_api_base_url` and `local_file_base_url` were normalized with a trailing slash, but `python-telegram-bot` concatenates the bot token directly onto `base_url`. The Local Bot API Server therefore rejected the token with `Unauthorized` and the bridge retried ten times and stopped. The documented profile in `deploy/telegram-local-api.env.example` and the built-in defaults both went through that normalizer, so the local transport had never functioned for anyone.
- The existing local-transport tests asserted only the resolved values and never the path the library actually requests, which is why the defect survived.

### Verification

- Added a regression test that starts a real HTTP server on a real socket, has the real Telegram library request through the resolved `base_url`, and asserts the requested path is exactly `bot<token>/getMe`. The assertion therefore tracks library behaviour instead of assuming it.
- Added coverage for the no-trailing-slash contract, for idempotent normalization of repeated trailing slashes, and for the supported environment-variable names in the deployment profile.

### Local Bot API Server

- Verified the Local Bot API Server on the production host: the bridge now runs against `127.0.0.1:8081` with a dedicated unprivileged system account, a hardened unit, and root-only credentials.
- The raise limit moves from 50MB to 2000MB and downloads are no longer bounded by server memory, which unblocks large-file delivery.
## [2.0.0-rc.4] - 2026-09-29

### User preferences now do something

- `notification_level` reaches `LiveProgressReporter`: `silent` suppresses live progress edits, `errors` shows only failures, and the final result is always delivered regardless of level.
- `output_style` reaches `render_progress(detail=...)`, so `full` renders the event log and `summary`/`compact` stay on the compact form.
- `retention_days` is published atomically to `runtime/retention-days` and read by the root maintenance script, so the attachment deletion window is owner-controlled instead of a hardcoded 7 days.

### Safety fix in the maintenance script

- The retention window was validated with `tr -dc '0-9'`, which sanitizes a malformed value instead of rejecting it: `14d` would have silently become 14 days in a root script that deletes files. Validation now strips whitespace only and requires the whole string to be digits, falling back to the default with a warning.
- Values outside `1..365` and any non-numeric content are rejected and covered by tests that execute the resolver with real bash.

### Settings UI

- Added three real choice sub-screens under the settings panel, registered automatically from `_OPTION_FIELDS`.
- The write path goes through an explicit allowlist, so an unknown field or an out-of-range retention is refused by the service.
- The settings panel still reports, and does not offer toggles for, the fields the runtime does not read.

### Verification

- 13 new end-to-end tests on a real database prove each preference has a real effect, that a missing, throwing, wrongly typed, or async provider falls back correctly, that the final render is always allowed, and that values survive a restart.
- Total suite: 476 tests.
## [2.0.0-rc.3] - 2026-09-29

### Telegram UX

- Added a /menu control panel that gathers every capability behind inline buttons: settings, model and reasoning level, tasks, schedules, system health, downloads placeholder, and grouped help.
- Every non-root screen ends with a back button, and every destructive or state-changing action requires an explicit confirmation step.
- The model panel hands off to the existing picker through a raw mdl:open callback, so model selection keeps its own short-payload path.
- Panel callbacks are pnl:go:/pnl:do:/pnl:yes:/pnl:no: with short names only; ssert_short() fails the build if any button would exceed the 64-byte Telegram limit.

### Architecture

- ridge/telegram/panels/registry.py holds the typed panel primitives and callback encoding.
- ridge/telegram/panels/router.py owns rendering, navigation, confirmation, and the per-press authorization re-check that CallbackQueryHandler requires.
- ridge/telegram/panels/screens.py holds the screens; they never import ot.py and receive every value by name through PanelContext, so each screen is testable with plain callables.
- ot.py is the only place binding real services to panel providers, and uild_panels() is the single registration point.
- No preference toggle is offered for a field the runtime never reads: eight UserPreferences fields are stored but unconsumed, so the settings screen reports effective values instead of pretending to control them.

### Verification

- Added 18 end-to-end tests driving the real router against a real SQLite database created by the real migration runner, the real task/session/preference stores, and a real HTTP server on a real socket standing in for OpenCode, so httpx, auth, and JSON handling are genuinely exercised.
- Covered the menu, every screen, the callback length bound, navigation, unknown screens, authorization on both the command and the press path, real failed tasks from the database, the retry flow, confirmation/cancel/delete, schedule toggling, stale buttons, and cross-panel handoff to the model picker.
## [2.0.0-rc.2] - 2026-09-29

### Fixes

- Fixed a total runtime failure shipped in `v2.0.0-rc.1`: `workspace_runtime.install()` closed the previous OpenCode client while the module-level `AgentService` in `bot.py` still referenced it, so every command and task failed with `RuntimeError: Cannot send a request, as the client has been closed`.
- Corrected reasoning-level strength order to match the OpenCode selector: `Max` outranks `Xhigh` instead of the reverse. This also changes the automatic choice and the daily scout.

### Telegram UX

- `/model` now lists active zero-cost models as inline buttons, and picking one shows its reasoning levels as a single list ordered strongest to weakest.
- Every level is labelled with its technical English name and its Arabic translation, with `Default` offered for the automatic level.
- Added an explicit automatic button that clears the manual choice and returns the session to the strongest free catalog model.
- Callback payloads carry only an index (`mdl:m:0`, `mdl:v:0:2`) so they stay far below the 64-byte Telegram limit and cannot be edited to name a paid or removed model.

### Model selection

- Added `model_variant` and `model_pinned` to `UserPreferences`, stored in the existing `user_settings` JSON blob with no schema migration.
- `ModelManager.best_available()` accepts an owner and consults the owner pin before the configured default and the catalog best.
- `reconcile_once()` and `force_all_sessions()` skip pinned owners, so a manual choice is no longer overwritten every 900 seconds or by the daily scout.
- Added `resolve_variant()` so an explicitly chosen reasoning level is forwarded with each prompt after being revalidated against live catalog metadata.
- The manual choice stays owner-scoped and never mutates the global preference.

### Verification

- Added 36 tests covering catalog-to-button mapping, index resolution, owner-scoped pins, pin survival across reconciliation and the daily scout, callback authorization, stale-selection handling, callback payload length, and the single-row bilingual level list.

## [1.8.2] - 2026-09-28

### Stable self-update

- Changed daily self-update to deploy the newest stable semantic-version release tag from the trusted repository instead of untagged `main`.
- Treats detached HEAD as the normal release deployment state.
- Preserves non-conflicting untracked files and existing stashes.
- Blocks updates on tracked local edits, conflicting untracked paths, divergent/newer local history, or active queued work.
- Validates the target release in a temporary detached worktree before activating it.

### Telegram

- Carries forward the v1.8.1 removal of the free-points indicator and local quota-tracking subsystem.

## [1.8.1] - 2026-09-28

### Telegram

- Removed the user-facing free-points estimate and per-command points usage header.
- Removed the local free-points tracker, quota-specific config variables, response metadata, audit fields, and maintenance-state reporting.

### Self-update

- Detached release checkouts can recover safely to `main` when history is fast-forward compatible.
- Non-conflicting untracked files are preserved instead of blocking the update.
- Tracked modifications, divergent history, and untracked paths that collide with `origin/main` still stop the update.
- Candidate revisions continue to run the full local validation suite in a temporary worktree before switching production.

### Verification

- Added regression tests preventing the points indicator from returning.
- CI, Code Quality, and Supply Chain Security remain required release gates.

## [1.8.0] - 2026-09-27

### Persistent Scheduled Jobs

- Added a dedicated persistent schedule registry in SQLite, separate from individual queued executions.
- Scheduled jobs are addressed by user-defined names instead of exposing internal queue IDs.
- Added one-time and recurring named schedules with restart-safe next-run state.
- Added list, show, rename, command replacement, command append, next-run edit, interval edit, manual run, pause, resume and delete controls.
- Added `/schedinterval <name> | once` to convert recurring jobs back to one-time behavior.
- Manual runs preserve the schedule definition and recurring executions remain serialized through the existing owner task queue.
- Schedule executions retain last-run and last-error state without deleting the persistent definition.

### Large Commands

- Scheduled commands are stored as unrestricted SQLite TEXT rather than bounded Telegram-state payloads.
- `/schedappend` can be used repeatedly to assemble very large commands across multiple Telegram messages.
- Added deterministic restart tests with scheduled prompts exceeding 60,000 characters.

### Telegram Task UI

- Removed user-visible task numbering and queue positions from ordinary task interactions.
- Reused one Telegram status message through queueing, live execution progress and final text delivery.
- Compact progress and persisted activity views omit internal task identifiers.
- `/cancel`, `/progress` and `/trace` operate on the current/latest task without requiring a numeric task ID.
- `/tasks` now shows active work and persistent schedules by readable state/name without exposing internal identifiers.

### Media Task Integration

- Captioned attachment tasks and research commands use the same single-message status lifecycle.
- Files sent without a caption remain silently staged until the instruction arrives.
- Existing SHA-256 attachment integrity checks, model-capability routing and managed output restrictions remain intact.

### Verification

- Added lifecycle tests for schedule persistence, case-insensitive names, editing, append, pause/resume, deletion, manual execution, one-time materialization, recurrence advancement and failure tracking.
- GitHub Actions remains the source of truth for Python syntax, unit tests, configuration validation and release publication.

## [1.7.1] - 2026-09-27

### Media Capability Routing

- Added task-local selection of active zero-cost OpenCode Zen models that explicitly advertise image input for visual attachment work.
- Visual media routing can override a text-only pinned/general model for the current task without changing the configured default for later tasks.
- Added live-catalog helpers that require explicit input capabilities and preserve the existing zero-cost Zen allow-list.

### Telegram Status UI

- Preserved the current unified one-message progress flow from `main`, including reuse of the same Telegram message for live status and the final result.
- Compact live and persisted progress views intentionally omit internal task identifiers.

### Documentation & Verification

- Clarified current OpenCode attachment behavior: text and supported raster images can be model-visible directly, while PDF/audio/video and other binaries require validated server-side inspection/conversion.
- Added deterministic tests for image-capability filtering and task-local media model selection.
- Corrected release-note source files so v1.7.0 describes only code actually contained in its published tag.
- GitHub Actions remains the source of truth for syntax, tests, OpenCode configuration and semantic-version validation.

## [1.7.0] - 2026-09-27

### Highlights

- Promoted Telegram images, video, audio and arbitrary documents to instruction-bound queued agent tasks.
- Added persistent no-caption staging so files can be sent first and automatically bound to the user's next text instruction.
- Added media-group batching so Telegram albums are processed as one coherent task.
- Added safe server-side binary-media routing through preinstalled inspection/conversion tools instead of assuming every provider can consume every binary format directly.

### Attachment Task Pipeline

- Captioned attachments execute immediately using the caption as the authoritative user instruction.
- Uncaptioned attachments are persisted in SQLite for a configurable bounded window and survive bridge restarts until an instruction arrives.
- Multiple staged files are merged into one task with duplicate-path suppression; `/discard` removes a pending batch before execution.
- Telegram media groups use a short configurable debounce window to avoid splitting one album into independent tasks.
- Model-visible text/image files use OpenCode file parts; PDF, audio, video and other binaries are handled from validated local paths using already-installed server tools when available.
- Binary inspection and conversions use an isolated per-task scratch directory that is removed on task completion.
- Managed outputs remain restricted to each task's outgoing directory and are returned to Telegram without exposing arbitrary server files.

### Integrity and Safety

- Every newly downloaded attachment receives a SHA-256 digest and is revalidated for path, size and content integrity before agent execution.
- Added configurable per-file, per-task-count and aggregate-byte limits.
- Attachment contents are explicitly framed as untrusted data so embedded file instructions cannot supersede the user's Telegram request.
- Incoming binaries, scripts and macros are not executed, dependency installation remains blocked, and original uploads are not modified.
- Pending files expire automatically; the existing seven-day managed-attachment cleanup remains a final retention guard.
- If a specialized parser/tool is unavailable, the agent is instructed to report the limitation rather than install software or invent results.

### Reliability

- Confirmation-message failures no longer discard a successfully queued task or a successfully persisted pending batch.
- Existing text-only tasks, scheduling, progress reporting, model fallback and adaptive worker behavior remain compatible.

### Configuration

- Added `TELEGRAM_ATTACHMENT_MAX_COUNT` (default `10`).
- Added `TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES` (default `52428800`).
- Added `TELEGRAM_ATTACHMENT_PENDING_SECONDS` (default `600`).
- Added `TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS` (default `1.25`).
- Synchronized the standard and V3 environment examples and documented the workflow in the deployment guide.

### Verification

- Added deterministic coverage for SHA-256 tamper detection, attachment task limits, prompt-injection framing, restart-safe pending batches, duplicate suppression and expiry cleanup.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, semantic-version validation and release publication.

## [1.6.0] - 2026-09-21

### Highlights

- Added a persistent two-line free-points usage header above final Telegram agent responses.
- Added per-command local usage metering for OpenCode assistant/model turns, including tool-call loops and retries.
- Included daily Agent Scout and autonomous maintenance-agent requests in the same free-tier accounting.
- Consolidated and deleted all non-`main` GitHub branches after verifying their work was already integrated.

### Free-Points Visibility

- Final Telegram responses now show the estimated daily remaining points and points consumed by the current command in exactly two lines.
- Usage persists in `runtime/free-points.db` across service restarts and resets automatically on the next configured local day.
- `OPENCODE_FREE_DAILY_POINTS` defines the local daily estimate and defaults to `200`.
- The remaining value is explicitly labelled as estimated because OpenCode Zen does not currently expose an authoritative remaining-free-quota counter to clients.
- Upstream free-tier exhaustion responses immediately set the local remaining estimate to zero.

### Usage Metering

- The bridge measures newly created assistant/model turns around each OpenCode prompt rather than flattening every user command to one point.
- Tool-call loops, automatic retries, and multi-turn agent work therefore contribute to the command's displayed consumption.
- Daily Agent Scout and maintenance-agent traffic use the same persistent accounting database.

### Repository Consolidation

- Verified every historical branch against `main` before cleanup.
- Confirmed the squash-merged free-points branch was byte-equivalent at the Git tree level before deletion.
- Deleted all non-default branches from GitHub.
- Used a scoped one-shot cleanup workflow and removed it immediately after successful branch deletion.
- The repository now contains only the `main` branch.

### Verification

- Feature PR CI succeeded before merge.
- Main-branch CI succeeded after the free-points feature merge.
- Branch cleanup completed successfully and the final branch inventory was verified through the GitHub API.
- GitHub Release publication remains gated on successful CI for the final release commit.

## [1.5.0] - 2026-09-21

### Highlights

- Added production-ready Telegram control for the OpenCode development agent with per-task reasoning-variant forwarding and stronger repository-development instructions.
- Added daily automatic selection of the strongest verified zero-cost OpenCode Zen model, using the highest supported reasoning variant exposed by live catalog metadata.
- Added autonomous daily host maintenance, safe package updates, cleanup, guarded self-update, and a read-only post-maintenance agent audit.
- Added boot-persistent ZRAM sized to 50% of physical RAM by default without the previous 2 GiB cap.

### Autonomous Model Management

- Agent Scout now re-evaluates zero-cost Zen models every day rather than permanently pinning a single model.
- The bridge dynamically selects recognized maximum reasoning variants such as `xhigh`, `max`, or `high` when the selected model advertises them.
- Unsupported variants retry safely without the variant before switching models.
- Muse Spark 1.3 Contributor Free remains a safe fallback configuration.

### Maintenance and Self-Update

- Daily maintenance now covers APT refresh and safe upgrades, autoremove, cache cleanup, temporary files, journal retention, and managed attachment cleanup.
- Self-update accepts only the trusted `Alaa91H/opencode-bridge` origin and clean fast-forward updates from `main`.
- Candidate revisions are verified in a detached temporary worktree using Python syntax checks, unit tests, OpenCode JSON validation, and shell syntax checks before deployment.
- systemd maintenance assets are synchronized after verified updates and unsafe local/divergent states stop automatic deployment without discarding local work.

### ZRAM and Resource Reliability

- ZRAM is installed and enabled automatically as a boot-level system service.
- The default compressed-swap target is 50% of detected physical RAM with no artificial 2 GiB ceiling.
- `zstd` is preferred when supported, then `lz4`, and ZRAM keeps a higher swap priority than disk fallback.
- Active ZRAM is resized only when no swap pages are in use; otherwise the new target is deferred safely until reboot.

### Repository Consolidation

- Reconciled all existing development branch heads to the current `main` state after verifying that squash-merged and legacy V3 work was already integrated or superseded.
- Preserved the newer modular `run_v3.py` production path instead of restoring the obsolete legacy `bot_v3.py` entrypoint.

### Verification

- CI validates Python syntax, the complete unit-test suite, maintenance shell scripts, both OpenCode configurations, and semantic-version format.
- GitHub Release publication remains gated on successful CI for `main`.

## [1.4.0] - 2026-09-14

### Highlights

- Added a unified 0–100 host health score that combines memory headroom, Linux memory PSI, swap pressure, normalized CPU load, and free disk capacity without replacing the existing production admission guardrails.
- Added explicit health levels (`healthy`, `degraded`, `high`, and `critical`) plus a shadow worker recommendation so score-based concurrency can be evaluated safely before it is allowed to affect production.
- Added rolling shadow-policy readiness evaluation with agreement, mean/max worker delta, aggressive-vs-conservative bias, and continuous-stability gates before any future promotion can be considered.
- Extended `/resources` diagnostics with controller health, shadow readiness, worker-delta evidence, and advisory promotion state.

### Adaptive Worker Reliability

- Production now keeps a single stabilized adaptive controller instead of bypassing it during V3 bootstrap.
- Resource-pressure reductions apply immediately, while recovery uses hysteresis and a configurable cooldown so worker counts increase gradually instead of oscillating near thresholds.
- `AGENT_TASK_WORKERS` remains the hard administrative ceiling and running tasks are never cancelled solely to reduce concurrency.
- Adaptive decisions expose stable workers, raw targets, pressure, health score, recovery time, and decision reason without read-only diagnostics mutating the production controller state.

### Shadow Evaluation and Audit

- Added low-noise `adaptive_worker_shadow_divergence` audit events for meaningful production-vs-shadow divergence transitions instead of logging every sample.
- Added `adaptive_worker_shadow_readiness` events only when advisory readiness changes between ready and not-ready states.
- Added bounded rolling comparison metrics including agreement percentage, aggressive and conservative samples, average absolute delta, and maximum absolute delta.
- Promotion remains advisory only in this release; the shadow policy cannot change production worker admission.

### Restart-Safe Evidence

- Added bounded persistence for recent shadow-readiness evidence under `runtime/shadow-readiness.json` with atomic replacement and restrictive file permissions.
- Recent validated evidence can survive short service restarts without counting downtime as additional stable time.
- Stale, malformed, future-dated, oversized, or otherwise invalid persisted state is rejected and evaluation restarts safely.
- Persistence and audit failures are non-disruptive and can never override production resource admission decisions.

### Verification

- Added deterministic coverage for health-score boundaries, shadow-level hysteresis, production/shadow divergence telemetry, rolling readiness gates, sustained stability, diagnostics visibility, restart-safe persistence, stale-state rejection, and runtime-path derivation.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, semantic-version validation, and release publication.

## [1.3.0] - 2026-09-14

### Highlights

- Upgraded adaptive worker management from one-time startup sizing to continuous live admission control in the V3 task worker pool.
- Workers now re-evaluate host pressure before claiming each new task, allowing concurrency to contract under memory, swap, CPU, PSI, or disk pressure and expand again after recovery without restarting the bridge.
- Preserved `AGENT_TASK_WORKERS` as the administrator-defined hard ceiling instead of permanently shrinking it during bootstrap.

### Reliability and Safety

- Running tasks are never cancelled merely because resource pressure increases; only new task claims are throttled.
- Adaptive sampling failures fail safely to one worker rather than allowing unbounded concurrency during uncertain host state.
- Disabled workers remain alive and periodically re-check the live policy, avoiding destructive process churn and enabling automatic recovery when pressure subsides.
- Existing per-owner serialization, workspace isolation, direct-to-`main` development policy, and no-local-build guardrails remain unchanged.

### Resource Policy

- The live controller continues to use the lightweight `HostResourcePolicy`, including available memory, Linux memory PSI, normalized CPU load, disk headroom, and swap occupancy.
- Sustained swap use now participates directly in worker throttling: high swap usage reduces concurrency and near-exhausted swap forces single-worker operation while ignoring very small swap devices.
- Resource reporting includes swap utilization so operators can correlate concurrency decisions with reclaim pressure.

### Verification

- Added deterministic tests proving that worker admission can change from three workers to one and later recover to four without recreating the task service.
- Added coverage proving the live limit cannot exceed the configured ceiling and provider failures fall back to one worker.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, semantic-version validation, and release publication.

## [1.2.5] - 2026-09-14

### Highlights

- Added a permanent Daily OpenCode Agent Scout to the existing `ModelManager`; it runs automatically every 24 hours without adding another daemon or local build workload.
- The scout inspects the live OpenCode primary-agent list and the live OpenCode Zen catalog, then considers only models whose provider metadata explicitly reports zero cost.
- Added a fresh web-research pass that asks OpenCode to compare the current free allow-list using recent official OpenCode information and reputable coding-agent benchmarks.
- The research result is accepted only when it exactly matches a currently active zero-cost model ID returned by the live provider catalog; otherwise the bridge falls back to its deterministic capability ranking.

### Global Default Switching

- The repository-specific `development-agent` remains the preferred primary agent because it carries the bridge's direct-to-`main`, CI, workspace-isolation, and no-local-build operating policy.
- If that primary agent is unavailable, the scout falls back to OpenCode's primary `build` agent before considering other visible primary-capable agents; subagents and hidden agents are never promoted to the global default.
- A successful daily decision updates the live bridge `DEFAULT_AGENT` and `DEFAULT_MODEL`, so existing conversations use the new agent on their next turn and every queued or newly-created task uses it when execution begins.
- Every saved OpenCode conversation is immediately patched to the selected free model. An inference already generating at the exact moment of a switch is allowed to finish safely and uses the new default from its next turn rather than being interrupted mid-response.
- The selected free model is pinned inside `ModelManager`, preventing the faster catalog-reconciliation loop from immediately replacing the researched daily choice while it remains active and free.

### Persistence and Safety

- The latest validated decision is stored in `runtime/agent-scout.json` with restricted file permissions and is restored after a service restart only if both the saved primary agent and saved model still exist in the current live OpenCode catalogs.
- Paid or unpriced models cannot be selected by the scout, even if a research response recommends them.
- A stale, removed, or newly paid model is automatically rejected on the next live catalog check and normal free-model selection takes over.
- Research failures are non-disruptive: the bridge keeps operating and selects the strongest free candidate from the live deterministic ranking instead of leaving users without an agent.
- The scout creates only a temporary OpenCode research session and removes it after the decision; no user conversation is reused for autonomous research.

### Configuration

- `AGENT_SCOUT_INTERVAL_SECONDS` controls the persistent research cadence and defaults to `86400` seconds (24 hours).
- `AGENT_SCOUT_PREFERRED_AGENT` defaults to `development-agent`.
- `AGENT_SCOUT_WEB_RESEARCH=1` enables the daily web-research comparison; setting it to `0` keeps the same free-only guardrails while using deterministic live-catalog ranking only.

### Verification

- Added tests proving that subagents and hidden agents cannot become the default, the project development agent is preferred, research output cannot inject a paid model, and a daily scout decision is applied to every saved conversation.
- Added coverage proving that an explicitly configured paid preference can never override the live zero-cost OpenCode Zen catalog.
- Fixed a test-isolation issue discovered by CI where intentional live-default mutation from the scout leaked into a later import test; production switching behavior remains unchanged.
- GitHub Actions remains the source of truth for syntax, unit tests, OpenCode configuration, version validation, and release publication.

## [1.2.4] - 2026-09-14

### Highlights

- Added a continuous V3 watchdog loop that evaluates OpenCode, the Telegram bridge, queue health, host resources, and repository deployment state without running application builds.
- Added deterministic health scoring with `healthy`, `degraded`, and `critical` states plus explicit manual-review escalation for risky conditions.
- Added bounded self-healing for the OpenCode user service while delegating Telegram process recovery to its existing systemd `Restart=on-failure` policy.
- Added structured watchdog reports and a dedicated redacted JSONL audit trail under `runtime/` for post-incident analysis.

### Safe Recovery Guardrails

- Automatic recovery is restricted to a fixed service allow-list and never permits SSH, firewall, reboot, package-management, destructive filesystem, or force-push actions.
- OpenCode restarts are blocked when available memory or disk headroom is critically low.
- Restart cooldowns and an hourly restart ceiling prevent feedback loops when a service remains unhealthy.
- Long-running tasks are never killed automatically; tasks that exceed the stale threshold are escalated for manual review instead.
- The watchdog never attempts to restart the Telegram bridge from inside the bridge process; systemd remains the single owner of bridge crash recovery.

### Health Signals and Observability

- The watchdog samples memory availability, swap, CPU load, disk headroom, and Linux memory PSI through the existing lightweight resource monitor.
- Queue health includes queued/running counts and stale-running detection from the SQLite task store using read-only access.
- OpenCode health combines systemd service state with the local HTTP health endpoint.
- Deployment health records whether the deployed revision matches the working revision and whether the local checkout trails the known `origin/main` reference.
- Runtime tuning is configurable with `WATCHDOG_INTERVAL_SECONDS`, `WATCHDOG_STALE_TASK_SECONDS`, `WATCHDOG_RESTART_COOLDOWN_SECONDS`, and `WATCHDOG_MAX_RESTARTS_PER_HOUR`.

### Verification

- Added unit coverage for healthy scoring, dual-service critical outages, stale-task escalation, critical-resource restart blocking, unknown-service rejection, cooldown behavior, and restart-loop limits.
- Fixed a CI assertion discovered by the new policy tests by preserving the stricter classification: simultaneous OpenCode and Telegram outages remain `critical` rather than being downgraded.
- GitHub Actions remains the source of truth for complete verification and release publication.

## [1.2.3] - 2026-09-14

### Highlights

- Bound V3 OpenCode sessions and requests to the exact repository workspace selected by the Telegram bridge instead of relying on prompt text alone.
- Added per-async-task workspace scoping with `ContextVar`, preventing directory context from leaking between parallel workers.
- Added automatic session rebinding when the active repository changes, after a service restart, or when a stored session no longer matches the task workspace.
- Added strict validation of the bridge-generated `ACTIVE_WORKSPACE` block before any scoped OpenCode request is executed.

### Multi-Repository Isolation

- Every scoped OpenCode HTTP request now carries the workspace directory through the supported `x-opencode-directory` request context.
- Session-specific requests retain their workspace binding through a lightweight in-memory session-to-directory registry.
- Queued tasks keep the repository directory captured when they were created, so switching the active Telegram repository cannot silently redirect an already queued development task.
- Spoofed or mismatched workspace paths are rejected before execution when they do not resolve to the allow-listed repository location.

### Concurrency and Reliability

- Workspace context uses Python `ContextVar` isolation, so independent worker tasks can safely target different repositories at the same time.
- Workspace scope is always reset after the request context exits, preventing accidental cross-project contamination.
- The V3 bootstrap replaces the generic OpenCode client before the task service and model manager start, ensuring workspace-aware behavior is used throughout the production runtime.
- Existing direct-to-`main`, CI feedback, no-local-build, and repository allow-list policies remain unchanged.

### Verification

- Added deterministic tests for workspace header injection, context cleanup, session-directory restoration, valid trusted workspace parsing, and rejection of forged workspace paths.
- GitHub Actions remains the source of truth for full verification; no application build or dependency installation is performed on the control server.

## [1.2.2] - 2026-09-14

### Highlights

- Added a lightweight Linux host resource policy that samples available memory, swap, CPU load, disk headroom, and memory pressure-stall information without requiring privileged access.
- Added adaptive worker sizing during V3 startup so the agent no longer trusts a fixed concurrency value when the host is under pressure.
- Promoted direct-to-default-branch autonomous development as the standard publication workflow for the development agent.
- Added a lightweight GitHub Actions monitor and Telegram `/ci` command for the active workspace.
- Added a mandatory CI feedback loop so the agent waits for remote checks, inspects failures, applies the smallest safe repair, pushes the fix, and verifies CI again.
- Added the first optional ZRAM/swap resource-optimizer asset and a systemd unit as groundwork for autonomous host tuning.

### Development Automation

- The development agent now fetches remote metadata, identifies the repository default branch, fast-forwards clean workspaces, reviews diffs, writes professional English Conventional Commit messages, pushes without force, and verifies publication.
- Feature branches and pull requests are now opt-in instead of the default, matching the repository owner's direct-to-`main` workflow.
- `github_ci.py` can inspect or wait for GitHub Actions by repository, branch, commit SHA, and workflow name without running builds on the control server.
- CI failure summaries include failed jobs and steps so repair prompts can focus on the actual remote failure instead of guessing.
- `GITHUB_CI_WORKFLOW` can target a specific workflow, while an optional `GITHUB_TOKEN` raises REST API rate limits without being inserted into agent prompts.

### Performance

- Healthy hosts can retain the configured worker capacity while small-memory hosts automatically reduce concurrency to avoid OpenCode/Telegram memory contention.
- Hosts below 1.5 GiB RAM are capped to one worker; hosts below 3 GiB are capped to two workers.
- CPU saturation, low disk headroom, and Linux memory PSI signals further reduce the recommended concurrency when necessary.
- Resource sampling is cached to keep monitoring overhead negligible on small VPS deployments.
- GitHub CI monitoring is read-only and network-bound, adding no local compilation or dependency-heavy workload.

### Reliability and Safety

- Adaptive sizing is enabled by default and can be disabled with `AGENT_ADAPTIVE_WORKERS=0` when fixed concurrency is explicitly required.
- The configured `AGENT_TASK_WORKERS` remains the upper bound; automatic sizing only reduces unsafe concurrency and never exceeds the administrator-defined limit.
- Added deterministic unit coverage for CI success/pending states, failed-step extraction, repository validation, and adaptive resource policy behavior.
- Direct publication never permits force push, destructive Git cleanup, credential changes, or overwriting unrelated local modifications.
- The development cycle treats GitHub Actions as the source of truth whenever verification requires builds, dependency installation, packaging, or platform-specific compilation.

### Autonomous Host Roadmap

- Added a guarded ZRAM-first memory optimizer that can provision bounded compressed swap and a low-priority disk-swap fallback without overwriting an existing unknown swap file.
- Root-level activation remains intentionally separated from the unprivileged agent runtime while the installation path is hardened further.
- Follow-up work is tracked for live concurrency resizing, service health scoring, reversible self-healing, disk-pressure controls, and automated security/update posture.

## [1.2.1] - 2026-09-14

### Highlights

- Promoted the V3 multi-repository development agent from an optional architecture to the production Telegram service path.
- Promoted the hardened development-agent policy to the primary `opencode.json` configuration used by the existing OpenCode systemd service.
- Added safe repository selection, persistent active workspaces, automatic workspace context injection, and configurable bounded parallel task execution.
- Added a dedicated V3 runtime wrapper that reuses the mature bridge instead of duplicating Telegram, scheduling, model-selection, progress, and audit logic.
- Added a production-oriented environment template for repository allowlists, workspace location, and worker concurrency.

### Performance

- Independent owners can execute tasks concurrently through a bounded worker pool while the queue continues to serialize work for the same owner.
- Git workspace operations use per-repository locks to avoid synchronization races.
- SQLite workspace state uses WAL mode, normal synchronization, and a busy timeout to reduce contention on long-running VPS deployments.

### Reliability

- Added CI validation for Python syntax, the complete unit-test suite, both OpenCode configuration files, and semantic versioning.
- Repaired the CI workflow so jobs are created and executed reliably on `main` pushes.
- Reworked automated release publication so successful CI runs can publish GitHub Releases through the GitHub API-backed CLI flow.
- Added unit coverage for GitHub URL normalization, repository allowlisting, and workspace-root isolation.

### Security and Operational Controls

- Repository access is limited by `GITHUB_ALLOWED_REPOS` and all managed working copies stay under `GITHUB_WORKSPACE_ROOT`.
- The development agent denies secret/key files and blocks local builds, dependency installation, force pushes, destructive Git operations, credential changes, release merges, and system power operations.
- The control server remains an AI development/orchestration host; builds and dependency-heavy verification stay in CI rather than on the VPS.

### Deployment Notes

- The Telegram systemd unit now starts `run_v3`, which layers V3 capabilities over the existing bridge runtime.
- Existing OpenCode service wiring remains compatible because the production `opencode.json` now contains the V3 development-agent configuration.
- Configure `GITHUB_ALLOWED_REPOS`, `GITHUB_WORKSPACE_ROOT`, and optionally `AGENT_TASK_WORKERS` before enabling repository development workflows.

## [1.2.0] - 2026-09-14

### Highlights

- Activated the bounded parallel task worker pool in the production task service while preserving strict per-owner task ordering.
- Added environment-based worker and polling controls so throughput can be tuned without code changes.
- Added a GitHub Actions CI pipeline covering Python 3.11, 3.12, and 3.13 with syntax, unit-test, configuration, semantic-version, and shell-script validation.
- Prepared the repository for CI-gated automated tagging and GitHub Releases.
- Continued the V3 development-agent architecture with repository-focused prompts and persistent multi-repository workspace management.

### Performance

- Independent users can now execute agent tasks concurrently instead of sharing a single global serial worker.
- Worker concurrency is bounded to protect small VPS hosts and can be configured with `AGENT_TASK_WORKERS`.
- Idle queue polling is configurable with `AGENT_TASK_POLL_SECONDS` to balance responsiveness and resource usage.

### Reliability and Safety

- The queue continues to prevent concurrent execution for the same owner, keeping task order deterministic.
- CI now validates every push to `main` before an automated release can be published.
- Server-side build restrictions remain unchanged: development orchestration may edit, inspect, commit, and manage repositories without performing application builds on the control server.

### Upgrade Notes

The default configuration uses two task workers and a five-second idle poll interval. Existing deployments remain compatible; no database migration is required.

## [1.1.13] - 2026-08-22

### ملخص

إصلاح توافق حقل النموذج مع رسائل OpenCode ومنع خطأ 400

### التغييرات منذ v1.1.12

- fix: send OpenCode model reference as object (c9c780d)

## [1.1.12] - 2026-08-22

### ملخص

اختيار تلقائي للنموذج العام الأفضل ومزامنة توفر نماذج Zen

### التغييرات منذ v1.1.11

- feat: automatically reconcile best Zen model (4a66e02)

## [1.1.11] - 2026-08-22

### ملخص

إضافة أوامر بحث صريحة وتوجيه نية متدرج مع تحقق من المصادر

### التغييرات منذ v1.1.10

- feat: add intent-aware research commands (60713c4)

## [1.1.10] - 2026-08-22

### ملخص

تصفير عدّاد مهام اليوم تلقائيًا عند بداية اليوم مع حفظ السجل الكامل

### التغييرات منذ v1.1.9

- feat: reset displayed task count daily (45f04ea)

## [1.1.9] - 2026-08-22

### ملخص

تطوير البحث إلى منهج متدرج وموثّق مع تحقق متقاطع ومصادر ومستويات عمق تلقائية

### التغييرات منذ v1.1.8

- feat: add evidence-first deep research workflow (317952f)

## [1.1.8] - 2026-08-22

### ملخص

تحويل نافذة تقدم المهمة إلى عرض معلومات فقط دون أزرار أو إرشادات تحكم

### التغييرات منذ v1.1.7

- ui: make task progress informational only (08d3804)

## [1.1.7] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- fix: mark root maintenance scripts executable (b33ca6c)
- release: v1.1.6 (97bd38c)
- fix: invoke root maintenance installer by absolute path (237b7ee)
- release: v1.1.5 (2040769)
- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.6] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- fix: invoke root maintenance installer by absolute path (237b7ee)
- release: v1.1.5 (2040769)
- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.5] - 2026-08-22

### ملخص

تشغيل الصيانة بصمت وإضافة حارس إعادة تشغيل مؤجل

### التغييرات منذ v1.1.4

- feat: run maintenance silently with guarded reboot flow (29748dd)

## [1.1.4] - 2026-08-22

### ملخص

قصر قائمة النماذج المجانية على OpenCode Zen

### التغييرات منذ v1.1.3

- feat: limit model catalog to free OpenCode Zen models (7abf7c2)

## [1.1.3] - 2026-08-22

### ملخص

تبسيط رسالة البداية وإزالة النصوص التشغيلية من المساعدة

### التغييرات منذ v1.1.2

- fix: keep start message minimal and remove operational help text (9dec6f2)

## [1.1.2] - 2026-08-22

### ملخص

عرض النماذج المجانية ذات التكلفة الصفرية فقط

### التغييرات منذ v1.1.1

- feat: list only zero-cost models in bot catalog (16b8433)

## [1.1.1] - 2026-08-22

### ملخص

تعيين Muse Spark 1.2 كنموذج افتراضي للبوت

### التغييرات منذ v1.1.0

- config: default bot sessions to Muse Spark 1.2 (c03b09b)

## [1.1.0] - 2026-08-22

### ملخص

إضافة تقدم حي وسجل نشاط آمن وتحكم تفاعلي بالمهام

### التغييرات منذ v1.0.2

- feat: add safe live task progress to telegram (c0f0228)

## [1.0.2] - 2026-08-22

### ملخص

إصلاح استئناف النشر وتفعيل مسار الإصدار المتحقق

### التغييرات منذ v1.0.1

- fix: resume pending deployment from deployed revision (b020ab2)

# سجل التغييرات

## [1.0.1] - 2026-08-22

### ملخص

إضافة مستودع Git محلي ومسار إصدار ونشر وتراجع متحقق

### التغييرات منذ v1.0.0

- chore: add verified release workflow (fef393d)
