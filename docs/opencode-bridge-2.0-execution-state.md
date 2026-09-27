# OpenCode Bridge 2.0 — حالة التنفيذ

> المرجع الملزم: `docs/opencode-bridge-2.0-execution-plan-ar.md`

## المرحلة الحالية

- المرحلة المغلقة: **T00 — تثبيت Baseline للإصدار 2.0**
- حالة T00: **مكتملة**
- المرحلة التالية: **T01 — تحديث Runtime والاعتماديات**
- حالة T01: **لم تبدأ بعد عند إنشاء هذا السجل**
- قاعدة الانتقال: لا يجوز بدء T02 قبل إغلاق T01 بالكامل بنفس الصرامة.

## تقدم T00

- [x] إنشاء وثيقة Architecture Baseline للحالة الحالية.
- [x] تسجيل جميع جداول SQLite الحالية ومخططها.
- [x] تسجيل جميع متغيرات البيئة الحالية، مع فصل الإعدادات الموثقة عن مراجع الصيانة/الداخلية.
- [x] حصر أوامر Telegram الحالية في مسار الإنتاج الفعلي `run_v3`.
- [x] حصر جميع خدمات systemd والمؤقتات.
- [x] تسجيل حدود المرفقات الحالية.
- [x] تسجيل سياسات OpenCode الحالية.
- [x] إنشاء اختبارات regression للوظائف الأساسية قبل refactor.
- [x] إنشاء benchmark baseline للذاكرة والـCPU وقاعدة البيانات.
- [x] توثيق نتائج baseline وCI على commit إغلاق التنفيذ البرمجي لـT00.

## أدلة T00

### المرجع والخطة
- ملف الخطة: `docs/opencode-bridge-2.0-execution-plan-ar.md`
- commit إنشاء المرجع: `049020b62db93180dcde9155f74df19f367d8227`

### وثيقة baseline
- `docs/v2-baseline-architecture-ar.md`
- توثق production entrypoint، architecture، الجداول، env، Telegram commands، systemd، attachment limits، OpenCode policy، workers وCI.

### Regression baseline
- `tests/test_v2_baseline_contract.py`
- يثبت production `run_v3` path، الجداول الستة، attachment limits، OpenCode deny policy، schedule API، systemd units، وsingle-message V3 task lifecycle.

### Probe / benchmark
- `scripts/v2_baseline_probe.py`
- inventory: modules/tests/commands/environment references/SQLite/systemd/attachments/OpenCode policy.
- benchmark: CPU SHA-256 throughput + peak RSS + SQLite WAL inserts + indexed point selects.

### إصلاح baseline لازم قبل التثبيت
- تم إصلاح `v3_plugin.py` كي لا يعرض task ID أو queue position وكي يستخدم نفس رسالة الحالة الواحدة.
- commit: `eb0fcce01c26ac47db4794d2fc6c73cb9bae8308`

### CI
- commit التنفيذ المتحقق منه: `840c25f15dad17cf31cda15a4353380e686bcd9d`
- GitHub Actions CI: https://github.com/Alaa91H/opencode-bridge/actions/runs/36353649290
- النتيجة: **success**
- نجح:
  - Install runtime dependencies
  - Validate Python syntax
  - Run unit tests
  - Record v2 baseline inventory
  - Run v2 baseline micro-benchmark
  - Validate shell maintenance scripts
  - Validate OpenCode configuration
  - Validate V3 OpenCode configuration
  - Validate version

## سجل التنفيذ

### 2026-09-27 — T00
1. تم إنشاء الخطة المرجعية الدائمة.
2. تم إنشاء ملف حالة التنفيذ.
3. تم جرد production path الحقيقي `python -m run_v3`.
4. تم جرد SQLite وTelegram commands وsystemd وenvironment وattachments وOpenCode policies.
5. تم اكتشاف مسار V3 قديم ما زال يعرض أرقام الطابور؛ تم إصلاحه قبل تثبيت baseline.
6. تم إنشاء architecture baseline.
7. تم إنشاء regression contract.
8. تم إنشاء reproducible inventory + micro-benchmark probe.
9. تم تشغيل probe وbenchmark داخل CI.
10. نجح CI بالكامل على commit `840c25f15dad17cf31cda15a4353380e686bcd9d`.
11. **T00 مغلقة.**

## الخطوة التالية المسموحة فقط

بدء **T01 — تحديث Runtime والاعتماديات** بقراءة ملف الخطة كاملًا أولًا، ثم:
1. تثبيت الإصدارات المستقرة الحالية من المصادر الرسمية.
2. إضافة Python 3.12/3.13/3.14 CI matrix.
3. ترقية الاعتماديات stable فقط.
4. إضافة reproducible dependency constraints/lock.
5. Dependabot أو Renovate.
6. upgrade tests وإزالة deprecation warnings الحرجة.
7. عدم بدء T02 حتى نجاح كامل Acceptance T01.
