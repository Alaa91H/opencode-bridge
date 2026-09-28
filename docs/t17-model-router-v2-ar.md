# T17 — Model Router v2

ModelRouter يختار النموذج لكل مهمة دون تعديل preference عامة.

Eligibility تمنع نموذجًا غير available أو بلا quota أو يفتقد capability مطلوبة أو context window كافية. Scoring ديناميكي يجمع capability وquality وlatency وreliability وcost وcontext headroom وquota ويخصم recent failure history.

ModelTask يدعم text/image/coding/long-context/reasoning كcapabilities عامة، مع أوزان latency/cost وpreferred_model خاص بالمهمة. النتيجة ModelRoute تحتوي primary وfallback chain وscores قابلة للرصد.

replace_catalog يسمح بتغير catalog أثناء التشغيل؛ الاختبارات تثبت أن القرار التالي يستخدم catalog الجديدة فورًا.
