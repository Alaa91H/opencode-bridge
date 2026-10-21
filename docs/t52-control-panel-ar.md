# T52 — لوحة التحكم بالأزرار

> المرحلة: T52 بعد T51 في `docs/opencode-bridge-2.0-execution-plan-ar.md`
> الحالة: منفّذة على `v2.0.0-rc.3`

## الهدف

بدل حفظ أوامر تيليجرام، كل شيء يصير أزرار: تختار القسم، تشوف الحالة الحقيقية، تتحكم، وترجع. كل شاشة بتعرض بيانات حيّة من نفس مصادر الـ runtime.

## البنية

```
bot.py ──▶ PanelRouter ──▶ Panel (شاشة) ──▶ PanelContext ──▶ providers
                 │                              │
                 │                              └── دوال async مربوطة بالأ(owner)
                 └── actions ──▶ TaskQueueStore / UserPreferencesService
```

| الوحدة | المسؤولية |
|---|---|
| `panels/registry.py` | `Panel` و`PanelAction` و`PanelView` وترميز `callback_data` |
| `panels/router.py` | `PanelRouter` و`PanelContext`، التوجيه، التأكيد، الصلاحية |
| `panels/screens.py` | الشاشات نفسها |

## قاعدة الطبقات

- `bridge/services/**` لا يستورد `telegram` إطلاقًا.
- الشاشات لا تعرف `bot.py`. تطلب من `PanelContext` قيمة باسم مفتاح، وكل مزوّد دالة async.
- `bot.py` هو المكان الوحيد الذي يربط الخدمات الحقيقية بالمفاتيح.
- `panels/registry.py` يستورد `telegram` — وهو الوحيد المسؤول عن `InlineKeyboardMarkup` (مع `rendering/model_picker.py` و`rendering/schedule_browser.py`).

لهذا كل شاشة تُختبر بحقول callable عادية بدون أي mock للخدمات.

## ترميز الأزرار

حد تليجرام 64 بايت. الشاشات بتتكلم بأسماء قصيرة وبلا قيم حرة:

| الزر | Payload | بايت |
|---|---|---|
| انتقال لشاشة | `pnl:go:system` | 13 |
| إجراء | `pnl:do:tasks:retry:25` | 21 |
| تأكيد | `pnl:yes:schedules:delete_schedule:7` | 36 |
| إلغاء التأكيد | `pnl:no:schedules` | 15 |
| زر خام | `arg` كما هو | — |

`assert_short()` تُستدعى وقت البناء، فأي تجاوز يفشل فورًا بدل ما يفشل عند المستخدم. الاختبارات تتحقق أن كل payload على كل شاشة أقل من 64 بايت.

`verb == "raw"` يسمح لزر بلوحة بقراءة callback من subsystem آخر (شاشة «النموذج» ترسل `mdl:open` لمُنتقي الموديلات، فيبقى مسار `mdl:` وحده).

## الشاشات

| الشاشة | مصدر البيانات |
|---|---|
| `menu` | ثابتة |
| `settings` | `ConfigurationService.public_config` + `UserPreferencesService.get` |
| `model` | مُوجّه إلى مُنتقي الموديلات |
| `tasks` | `TaskApplicationService.active` + `.failed` |
| `schedules` | `TaskQueueStore.list_scheduled_jobs` |
| `system` | `HealthService` + `ConfigurationService.effective_limits` |
| `downloads` | placeholder — التحميل في T53 |
| `help` | `core_commands()` مجمّعة حسب الوظيفة |

`system` بيبني تقرير صحة حقيقيًا: OpenCode عبر `/global/health`، القرص عبر `shutil.disk_usage`، قاعدة البيانات بقراءة فعلية، وعدّاد العمّال والكتالوج من الحالة الحيّة.

## قرار: لا تبديلات وهمية

`UserPreferences` فيه ثمانية حقول **لا يقرأها أي كود تشغيلي**: `language` و`notification_level` و`output_style` و`retention_days` و`timezone` و`default_workspace` و`default_execution_profile` و`schedule_defaults`. حقلّا `model_*` وحدهما مستهلكان فعليًا.

لذلك شاشة الإعدادات تعرض القيم الفعلية قراءةً، ولا تعرض أي زر تبديل لخاصية لا يقرأها الـ runtime. إضافة زر لـ `output_style` كانت ستعطي إحساسًا بالتحكم بدون أي أثر — وهذا أسوأ من غياب الزر.

العمل المتبقّي هو **ربط** هذه الحقول لا عرضها:
- `retention_days` → `ATTACHMENT_RETENTION_MINUTES` المصلّب في `maintenance/daily-maintenance.sh`
- `notification_level` → كتم رسالة التقدّم بدل إرسالها
- `output_style` → `render_progress(detail=...)`

## قرارات UX

- زرار واحد لكل خيار في القوائم الطويلة، وزرّان في السطر الواحد في الجداول القصيرة (مطابق لمبدأ `schedule_browser`).
- كل شاشة غير `menu` تنتهي بـ `‹ رجوع للقائمة`.
- كل إجراء مدمّر أو بيغيّر حالة بتمر بزر تأكيد: `⚠️` + `✅ تأكيد` + `❌ إلغاء`.
- `CallbackQueryHandler` يتجاوز `@authorized`، فالصلاحية تُفحص عند **كل** ضغطة في `PanelRouter.handle`.

## الصيانة

`build_panels()` هي نقطة التسجيل الوحيدة. شاشة جديدة = دالة async + سطر في `build_panels()` + مزوّد في `bot.py` إن احتاجت بيانات.

## E2E

`tests/test_v2_panel_e2e.py` — 18 اختبارًا على:

- SQLite حقيقي عبر `MigrationRunner` الحقيقي
- `TaskQueueStore` و`SessionStore` و`UserPreferencesService` حقيقية
- خادم HTTP حقيقي على socket حقيقي يحاكي OpenCode، فعميل `httpx` والمصادقة وJSON تُختبر فعلًا
- بوت تسجيل بدل شبكة تيليجرام وحدها

المغطّى: عرض القائمة، رسم كل شاشة، الطول الأقصى للـ callback، التنقل، الشاشة المجهولة، رفض غير المصرّح لهم (أمر وضغطة)، المهام الحقيقية من القاعدة، تدفّق إعادة المحاولة، التأكيد والإلغاء والحذف الفعلي، تبديل الجدولة، الزر المهجور، انتقال `mdl:open` بين اللوحتين، وثبات الاختيار بعد الحفظ.
