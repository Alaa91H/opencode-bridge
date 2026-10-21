# T29 — Observability كاملة

MetricsRegistry يوفر contract مركزيًا للـqueue depth، task latency/duration، retry count، model/Telegram failures، DB latency/locks، memory/CPU/disk، attachment throughput bytes، active workers، وschedule lag.

الواجهة dependency-light وthread-safe وتدعم set/inc/observe/snapshot. القيم التي لا يجوز أن تكون سالبة تُرفض مبكرًا. Resource sampler يجمع disk free وhost memory وnormalized CPU load بواجهات Python القياسية.

PrometheusEndpoint اختياري: عند التعطيل يعيد 404 ولا يفتح surface غير مطلوب، وعند التفعيل يعرض Prometheus text format بأسماء opencode_bridge_*.

المقاييس هنا primitives مشتركة يمكن للـservices/adapters الحالية حقنها في critical paths دون ربط domain بـPrometheus. اختبارات T29 تثبت وجود جميع المقاييس المطلوبة، counters/gauges/latencies، resource sampling، validation، والتفعيل الاختياري للـPrometheus.
