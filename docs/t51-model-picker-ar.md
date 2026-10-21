# T51 — اختيار النموذج ومستوى الاستدلال بالأزرار

> المرحلة: T51 بعد إغلاق T49 في `docs/opencode-bridge-2.0-execution-plan-ar.md`
> الحالة: منفّذة على `v2.0.0-rc.1`

## المشكلة

اختيار النموذج كان للعرض فقط: أمر `/model` يطبع ترتيب النماذج المجانية، وأمر `/model <اسم>` يرفض أي معامل ويقول إن الاختيار تلقائي. المستخدم كان يقدر يشوف الموديلات بس ما يقدرش يختار.

والأهم: حتى لو اختار، الاختيار كان هيتداس عليه خلال 15 دقيقة.

## لماذا كان الاختيار اليدوي مستحيلًا قبل هذه المرحلة

| الحلقة | الفترة | السلوك السابق |
|---|---|---|
| `ModelManager.reconcile_once()` | كل 900 ثانية | يحرّك كل جلسة خاملة إلى `best_available()` |
| `ModelManager.scout_once()` → `force_all_sessions()` | كل 24 ساعة | يكتب على **كل** الجلسات بدون استثناء |
| `AgentService.ensure_session_model()` | كل مهمة | تختار دائمًا `best_available()` |

`best_available()` كان يرجّع `pin_default_model` أو `_preferred_model` (عالمي) أو الأفضل من الكتالوج، ولا يعرف شيئًا عن تفضيل المستخدم. أي قيمة يكتبها المستخدم في `sessions.model` كانت ستُستبدل في الدورة التالية. لذلك لم يكن التثبيت اليدوي ممكنًا قبل إضافة `_owner_pin_reader`.

## التصميم

### 1. التخزين — بدون migration

`user_settings` يخزّن JSON blob واحد لكل مالك، و`UserPreferences.from_dict` يتجاهل المفاتيح المجهولة، فإضافة حقول جديدة متوافق للخلف بلا لمس المخطط:

```python
model_preference: str | None = None   # كان موجودًا وبلا استخدام
model_variant: str | None = None     # جديد
model_pinned: bool = False           # جديد
```

`__post_init__` يفرض: `model_pinned` و`model_variant` لا يصحان بدون `model_preference`، والقيم يجب أن تكون نصوصًا غير فارغة.

### 2. احترام الاختيار

- `ModelManager.set_owner_pin_reader()` يسجّل دالة قراءة التثبيت؛ `best_available(excluded_ids, owner_id)` يستشيرها **أولًا** قبل `pin_default_model` والأفضل من الكتالوج.
- `reconcile_once()` و`force_all_sessions()` يتخطّيان أي مالك مثبّت.
- `ensure_session_model()` يمرّر `telegram_user_id`، فالتثبيت يُطبَّق على جلسة صاحب الطلب.
- `ModelManager.resolve_variant()` يقرأ `model_variant` للمالك بعد التحقق أنه ما زال مدعومًا في الكتالوج الحي، ويرجع لأقوى مستوى مُعلن عند غيابه.
- `AgentService.resolve_variant()` يستخدمه في `send_for_model`، فالمستوى المختار يذهب مع كل طلب.

`variant_for_model()` المتزامن بقي كما هو للتوافق مع النماذج الوهمية في الاختبارات، والمسار الجديد يكتشف `resolve_variant` عبر `getattr` على المدير حتى لا تنكسر الاختبارات القائمة.

### 3. `callback_data` قصير

تليجرام يسمح بـ 64 بايت فقط، ومعرّف `opencode/muse-spark-1.3-contributor-free` وحده 37 حرفًا. لذلك الأزرار تحمل **فهرسًا** فقط ويُعاد قراءة الكتالوج الحي عند الضغط:

| الزر | Payload |
|---|---|
| اختيار موديل | `mdl:m:3` |
| اختيار مستوى | `mdl:v:3:2` |
| رجوع | `mdl:back` |
| تلقائي | `mdl:auto` |
| تنقل صفحات | `mdl:p:1` |

لا يوجد أي اسم موديل أو مستوى داخل `callback_data`، فلا يمكن التلاعب به ولا يتجاوز الحد.

### 4. حدود الطبقات

- `bridge/services/model_selection_service.py` لا يستورد `telegram` إطلاقًا؛ يتعامل مع `ModelSelectionView` و`ModelChoice` و`VariantChoice` فقط.
- `bridge/telegram/rendering/model_picker.py` هو الوحيد الذي يبني `InlineKeyboardMarkup`.
- `bridge/telegram/callbacks/model_picker.py` يعيد فحص `is_allowed` لأن `CallbackQueryHandler` يتجاوز `@authorized`.
- `bridge/telegram/commands/agent.py` لا يعرف شيئًا عن SQL.

### 5. المسارات

| المسار | السلوك |
|---|---|
| `mdl:m:<i>` | يثبّت الموديل، يحتفظ بمستوى صالح، يعرض مستويات هذا الموديل |
| `mdl:v:<i>:<k>` | `k=0` → تلقائي، `k>0` → المستوى `k-1` من ترتيب الأقوى للأضعف |
| `mdl:auto` | يمسح التثبيت ويعيد الجلسة لأفضل كتالوج |
| `mdl:back` / `mdl:p:<n>` | رجوع وتنقل |
| `mdl:noop` | زر الصفحة الحالية |

مستوى معطّل في الكتالوج (`disabled: true`) لا يظهر ولا يُقبل. موديل اختفى من الكتالوج بين العرض والضغط يُرفض بـ `ModelSelectionError` وتُحدَّث القائمة بدل ترك المستخدم على شاشة قديمة.

## حدود مقصودة

- الاختيار **لكل مستخدم** ولا يعدّل التفضيل العام. scout ما زال يختار الافتراضي للجميع، والمثبّت يُستثنى منه فقط.
- إذا ثبّت المستخدم موديل بأقل قدرات، اختيار الوسائط يستمر في مسار `best_available_for_inputs` كما كان (T17) — التثبيت يحكم المهام النصية.
- الاختيار لا يُطبَّق على جلسة مشغولة (سلوك `reconcile` الموجود): يُطبَّق من أول مهمة تالية.

## إصلاح مصاحب

`workspace_runtime.install()` كان يقفل عميل OpenCode القديم بعد استبداله، بينما `AgentService` اللي اتبنى وقت الاستيراد في `bot.py` ما زال يشير إليه. النتيجة `RuntimeError: Cannot send a request, as the client has been closed` على **كل** الأوامر والمهام في `v2.0.0-rc.1`. الإصلاح يعيد توجيه `service.client` قبل الإغلاق. هذا موجود في rc.1 المنشور، ومطلوب في أي إصدار تالٍ.
