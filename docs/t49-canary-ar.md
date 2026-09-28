# T49 — Canary

Canary يبدأ shadow: يحسب legacy وv2 scheduler occurrence keys ويقارنها دون تنفيذ v2. أي mismatch يدخل scheduler_mismatch metric ولا يسمح بالتوسعة.

بعد shadow الأخضر، CanaryPolicy يختار نسبة صغيرة من المهام deterministic حسب SHA-256 task key؛ البداية الافتراضية 1% ويمكن التوسع بخطوات مضبوطة حتى 100% فقط بعد مراقبة metrics.

thresholds الافتراضية: error rate <=2%، scheduler mismatch =0، وp95 latency ratio <=1.5 مقارنة بالbaseline. تجاوز أي threshold يعني rollback تلقائيًا ويجب ربط القرار بمسار T34 atomic rollback في deployment adapter.

metrics الأساسية تأتي من T29/T43، والاختبارات تثبت shadow comparison، deterministic small-percentage routing، gradual expansion، وrollback لكل threshold.
