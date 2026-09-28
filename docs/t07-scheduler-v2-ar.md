# T07 — Scheduler Engine v2

## الأنماط
المحرك النقي في `bridge/domain/schedules/engine.py` يدعم once وinterval وcron (خمسة حقول)
وdaily وweekly وmonthly وweekdays. الحساب الداخلي UTC، بينما تعريفات الساعة المحلية تستخدم
IANA `zoneinfo` للحفاظ على wall-clock عبر DST.

## Misfire
السياسات: `skip`, `run_once`, `catch_up`, `coalesce`.
`due_occurrences` يحول فترة downtime إلى occurrences حسب السياسة دون افتراض أن tick وصل مرة واحدة.

## Overlap
السياسات: `forbid`, `allow`, `replace`, `queue`.
`overlap_action` يحدد قرار التنفيذ بصورة مستقلة قابلة للاختبار. مسار legacy الحالي يستخدم
FORBID افتراضيًا حفاظًا على السلوك الآمن؛ occurrence المتداخل يسجل skipped في history بدل أن يختفي.

## Persistence وhistory
جدول `schedules` يحمل timezone/misfire/overlap/next/last state، و`schedule_runs` يحتفظ
بسجل التشغيل مع unique occurrence. أثناء نافذة التوافق تُنسخ تعريفات `scheduled_jobs`
إلى catalog v2 قبل كتابة history للحفاظ على foreign-key integrity.
`/schedhistory <name>` يعرض التاريخ للمستخدم عبر service/adapter دون SQL في Telegram handler.

## Recovery
`promote_due` يعمل بمعاملة فورية، يسجل occurrence durable قبل إنهاء المعاملة، وينشئ
UUID/idempotency key للمهمة المجدولة. restart ثم tick مكرر لا ينشئان نفس occurrence مرتين.
one-shot يعطل بعد materialization، وinterval يتقدم إلى أول موعد بعد now.

## DST
الاختبارات تغطي Europe/Berlin عند spring-forward وfall-back وتثبت أن 09:00 المحلية تبقى
09:00 محليًا رغم تغير UTC offset، إضافة إلى cron/weekly/monthly/weekdays.

## Compatibility
لا تزال أوامر الجدولة النصية القديمة مدعومة خلال الترحيل. واجهة الإدارة الحديثة والـInline
Keyboard والتعديل المرئي/pagination تقع في T08 ولا تُقدّم داخل T07.
