# T32 — Health / Readiness / Liveness

liveness مستقل عن dependencies ويعبر فقط عن حياة العملية. readiness يقيس قابلية الخدمة لاستقبال العمل اعتمادًا على المكونات المطلوبة.

HealthService يغطي Telegram وDB وOpenCode وdisk وworkers وscheduler وmodel catalog. كل ComponentHealth يملك healthy/degraded/unhealthy وrequired flag. فشل required يجعل health unhealthy ويمنع readiness؛ فشل optional يبقي الخدمة ready لكن health يصبح degraded.

systemd_readiness_message ينتج READY/STATUS payload صالحًا للربط مع sd_notify/systemd integration عندما تكون آلية الإشعار متاحة في deployment، دون جعل systemd dependency داخل domain.

اختبارات T32 تغطي استقلال liveness، جميع المكونات المطلوبة، required failure، degraded optional mode، ورسالة readiness لـsystemd.
