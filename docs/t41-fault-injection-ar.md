# T41 — Fault Injection

Fault matrix يغطي الحالات الملزمة: bot kill، OpenCode kill، Telegram outage، SQLite busy، disk full، corrupt attachment، HTTP 429/500، timeout، duplicate update، restart أثناء schedule، worker crash، output upload failure.

معيار كل fault هو بقاء committed task موجودًا بعد recovery؛ faults القابلة لإعادة المحاولة تعود queued، بينما corruption/disk exhaustion يمكن أن تصبح failed بصورة صريحة دون فقد السجل. duplicate input لا ينشئ committed task ثانية، وفشل upload يسمح بإعادة delivery.

هذه matrix تكمل crash/restart/circuit-breaker/idempotency tests الموجودة في T05/T06/T16 بدل نسخ implementation production داخل test harness.
