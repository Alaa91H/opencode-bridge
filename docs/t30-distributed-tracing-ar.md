# T30 — Distributed Tracing

TraceContext يستخدم W3C traceparent المتوافق مع OpenTelemetry: trace_id بطول 128-bit وspan_id بطول 64-bit وsampled flag. root ينشئ trace جديدًا، وكل child يحتفظ بنفس trace_id مع span_id مستقل.

المراحل الملزمة ممثلة صراحة: Telegram → DB → OpenCode → model → media → GitHub → result. propagation_headers ينقل traceparent إلى adapters التي تدعم headers، وContextVar ينقل السياق داخل مسار asyncio دون global mutable trace.

SamplingPolicy يقبل ratio من 0 إلى 1. Privacy redaction تمنع attributes الحساسة، تحجب prompt/file content، وتحوّل owner/chat/user identifiers إلى hash قصير بدل القيمة الأصلية.

التصميم لا يفرض OpenTelemetry SDK كاعتماد إنتاجي؛ العقد متوافق مع W3C/OpenTelemetry ويمكن ربط exporter/SDK اختياريًا لاحقًا دون تغيير domain أو correlation semantics.

اختبارات T30 تغطي end-to-end trace-id propagation للمراحل المطلوبة، child spans، ContextVar، sampling validation، وprivacy redaction.
