# T16 — Circuit Breakers

يوفر CircuitBreakerRegistry دوائر مستقلة لـ Telegram وOpenCode وmodel_provider وGitHub وstorage حتى لا يؤدي فشل dependency واحدة إلى فتح البقية.

الحالات: CLOSED ثم OPEN عند بلوغ failure_threshold، وبعد recovery_timeout يسمح probe في HALF_OPEN. نجاح العدد المضبوط half_open_successes يعيد CLOSED؛ أي failure في HALF_OPEN يعيد OPEN.

الإعدادات قابلة للضبط: failure_threshold وrecovery_timeout وhalf_open_successes. لكل دائرة metrics: successes/failures/rejected/opened/recovered، وcallback للأحداث open/half_open/closed.

اختبارات T16 تستخدم clock حتميًا وتثبت recovery وإعادة الفتح والعزل بين dependencies.
