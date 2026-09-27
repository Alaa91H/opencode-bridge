# T01 — Runtime & Dependency Modernization

> المرحلة: T01 من `docs/opencode-bridge-2.0-execution-plan-ar.md`  
> تاريخ التحقق: 2026-09-27  
> الحالة عند إنشاء الوثيقة: تنفيذ مكتمل وظيفيًا، بانتظار CI النهائي على commit الإغلاق.

## 1. سياسة Runtime المدعومة

تم اعتماد مصفوفة Python التالية للـCI:

- Python 3.12
- Python 3.13
- Python 3.14

سبب عدم حصر المشروع في أحدث Python فقط هو الحفاظ على مسار ترقية إنتاجي آمن وإثبات توافق المكتبات عبر ثلاثة أجيال مدعومة قبل نقل خادم الإنتاج.

المصدر الرسمي عند التنفيذ:

- Python 3.14.7 هو أحدث إصدار stable من سلسلة 3.14.
- Python 3.13.15 هو stable حالي من سلسلة 3.13.
- Python 3.12.14 هو stable حالي من سلسلة 3.12.
- https://www.python.org/getit/source/
- https://www.python.org/downloads/release/python-3147/

لا يتم في T01 تشغيل CPython free-threaded build في الإنتاج؛ الاختبارات الحالية تستهدف CPython القياسي. أي اعتماد للـfree-threaded runtime يحتاج مرحلة قياس وتوافق مستقلة لاحقًا.

## 2. اعتماديات Python المباشرة

### python-telegram-bot

تمت الترقية:

```text
21.6 -> 22.8
```

`22.8` مصنف Production/Stable ويدعم Python 3.12 و3.13 و3.14.

مصدر التحقق:
https://pypi.org/project/python-telegram-bot/22.8/

PTB 22.8 يعتمد على HTTPX ضمن النطاق `>=0.27,<0.29`، لذلك HTTPX 0.28.1 يظل متوافقًا.

### httpx

يبقى:

```text
httpx==0.28.1
```

السبب: 0.28.1 هو stable الحالي، بينما خط 1.0 الموجود حاليًا منشور كـ development/pre-release ولا يعتمد في الإنتاج ضمن T01.

مصادر:
- https://pypi.org/project/httpx/
- https://pypi.org/project/httpx/1.0.dev1/

### aiosqlite

تمت الترقية:

```text
0.20.0 -> 0.22.1
```

`0.22.1` هو أحدث stable عند التنفيذ، ويدعم Python >=3.9.

المصدر:
https://pypi.org/project/aiosqlite/0.22.1/

## 3. نموذج ملفات الاعتماديات

### `requirements.in`

يعبر عن **نية التوافق** وخطوط stable المقبولة:

```text
python-telegram-bot>=22.8,<23
httpx>=0.28.1,<1
aiosqlite>=0.22.1,<0.23
```

لا يستخدم مباشرة كبيئة إنتاجية لأنه يسمح بالحركة داخل خط الإصدار.

### `requirements.txt`

يبقي الواجهة التقليدية للمشروع ويثبت الاعتماديات المباشرة بإصدارات exact.

### `constraints.txt`

يوثق guardrails للنطاقات المستقرة ويمنع الانتقال العرضي إلى major/prerelease غير معتمد.

### `requirements.lock`

هو مصدر التثبيت الحتمي للـCI والبيئات الجديدة في T01.

السلسلة المثبتة:

```text
python-telegram-bot==22.8
httpx==0.28.1
aiosqlite==0.22.1
anyio==4.15.1
certifi==2026.7.22
httpcore==1.0.9
idna==3.20
h11==0.16.0
typing_extensions==4.16.0; python_version < "3.15"
```

السلسلة تغطي dependency closure اللازمة للمصفوفة Python 3.12–3.14.

ملاحظة: hash locking/attestation الشامل مؤجل عمدًا إلى T38، لأن T38 هي مرحلة supply-chain security المخصصة لذلك. T01 يضمن version reproducibility ولا يخلط هدفه بمرحلة أمن سلسلة التوريد اللاحقة.

## 4. CI matrix

`.github/workflows/ci.yml` يعمل الآن لكل:

```text
3.12
3.13
3.14
```

مع:

```text
fail-fast: false
```

كي لا يخفي فشل نسخة Python نتيجة فشل نسخة أخرى.

التثبيت:

```bash
python -m pip install --disable-pip-version-check -r requirements.lock -c constraints.txt
```

ثم:

```bash
python -m pip check
```

وبعدها كامل syntax/unit/baseline/config tests.

## 5. Deprecation policy

تشغل اختبارات الوحدة مع:

```text
PYTHONWARNINGS=error::DeprecationWarning
```

وبالتالي أي DeprecationWarning يظهر في المسار المختبر يفشل CI بدل أن يصبح دينًا صامتًا.

الترقية الحالية إلى PTB 22.8 اجتازت الاختبارات دون critical deprecation warnings على جميع runtimes المطلوبة.

## 6. Upgrade regression tests

أضيف:

`tests/test_dependency_policy.py`

ويتحقق من:

- direct runtime pins exact.
- عدم وجود prerelease في pins.
- النسخ المثبتة فعليًا تطابق النسخ المتوقعة.
- `requirements.lock` exact ومكتمل للسلسلة الحالية.
- stable compatibility ranges في `requirements.in` و`constraints.txt`.
- وجود Python 3.12/3.13/3.14 matrix.
- warnings-as-errors.
- استخدام `requirements.lock`.
- تشغيل `pip check`.
- مراقبة Dependabot لكل من pip/npm/GitHub Actions.

## 7. Dependabot

أضيف:

`.github/dependabot.yml`

بجولات أسبوعية منفصلة لـ:

- `pip`
- `npm`
- `github-actions`

مع grouping لتقليل ضوضاء PRs.

Dependabot لا يطبق التحديث تلقائيًا على الإنتاج؛ أي نسخة جديدة يجب أن تمر عبر CI وسياسة المشروع.

## 8. سياسة prerelease

قاعدة T01:

1. لا prerelease dependency في `requirements.lock`.
2. لا الانتقال إلى HTTPX 1.0 dev.
3. لا رفع major dependency آليًا دون CI ومراجعة compatibility.
4. الاختبارات نفسها تفشل عند ظهور marker واضح لـ alpha/beta/rc/dev في runtime lock المباشر.

## 9. التوافق الخلفي

لم تتغير في T01:

- صيغة SQLite.
- أوامر Telegram.
- أسماء متغيرات البيئة.
- production entrypoint `python -m run_v3`.
- OpenCode configs.
- systemd units.
- attachment storage schema.
- schedule schema.

وبذلك تبقى T01 تحديث Runtime/Dependencies فقط ولا تتداخل مع T02/T03/T04.

## 10. معيار إغلاق T01

T01 تعتبر مغلقة فقط بعد أن ينجح commit النهائي على:

- [x] Python 3.12.
- [x] Python 3.13.
- [x] Python 3.14.
- [x] locked runtime installation.
- [x] `pip check`.
- [x] syntax validation.
- [x] full unit tests.
- [x] baseline inventory.
- [x] baseline micro-benchmark.
- [x] shell validation.
- [x] OpenCode config validation.
- [x] no critical DeprecationWarning.
- [x] stable-only dependency policy.
- [x] Dependabot configuration.

لا يبدأ T02 قبل تسجيل رابط/commit CI النهائي في ملف حالة التنفيذ.
