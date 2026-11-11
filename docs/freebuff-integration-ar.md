# تكامل Freebuff CLI مع OpenCode Bridge

> الحالة: اختياري ومُعطَّل افتراضياً. OpenCode يبقى الخلفية الافتراضية.
> آخر تحديث: 2026-11-11

## 1) نظرة عامة

يستطيع البريدج الآن تنفيذ مهام مالك واحد عبر **Freebuff CLI** بدلاً من OpenCode،
دون تغيير واجهة Telegram أو الطابور. الاختيار لكل مستخدم عبر `/backend`.

**قيد جوهري:** Freebuff CLI تفاعلي (TUI) ولا يوفّر `--print` أو `--json` أو
`--headless` حتى تاريخ هذا المستند (الطلب upstream #947 مفتوح، وACP #1611 لم
يُدمج). لذلك يعمل التكامل كالتالي:

1. يكتب البريدج التعليمات الكاملة في
   `<FREEBUFF_WORKSPACE>/.bridge/prompts/<run>.md` بصلاحية `0600`.
2. يشغّل `freebuff --cwd <workspace>` داخل PTY، مع `--continue <chat-id>` عند
   وجود محادثة سابقة.
3. يكتب سطراً واحداً يشير إلى ملف التعليمات (لا تُحقن نصوص متعددة الأسطر في TUI).
4. يراقب الشاشة لاكتشاف: تسجيل الدخول، الانشغال، أخطاء الحصة، أو الاكتمال.
5. يستخرج الرد من
   `~/.config/manicode/projects/*/chats/*/chat-messages.json` (الأحدث)،
   و`/export` JSON كبديل، ويفشل مغلقاً إذا لم يجد نصاً موثوقاً.
6. يحذف ملف التعليمات والنسخ المؤقتة، ويقتل مجموعة العملية عند الإلغاء أو المهلة.

## 2) المتطلبات

- خادم Linux (PTY غير مدعوم على Windows).
- Python 3.12+ (نفس بيئة البريدج الحالية).
- Node.js وnpm لتثبيت Freebuff.
- Freebuff CLI إصدار `0.0.140` أو أحدث (الحد في `FREEBUFF_MIN_VERSION`).
- حساب Freebuff مسجّل الدخول **بمستخدم الخدمة نفسه** الذي يشغّل البريدج.
- مساحة عمل حقيقية (مجلد) يملكها مستخدم الخدمة.

## 3) التثبيت على الخادم

```bash
# بمستخدم الخدمة (مثال: ubuntu)
npm install -g freebuff
freebuff login          # مرة واحدة، يفتح المتصفح/الجهاز
freebuff --version      # يجب أن يطبع إصداراً >= 0.0.140
```

إذا لم يكن `freebuff` على `PATH`، اضبط `FREEBUFF_BIN` على المسار الكامل:

```bash
command -v freebuff
```

## 4) الإعداد

أضف إلى `/home/ubuntu/opencode-bridge/.env`:

```dotenv
# opencode (الافتراضي) | freebuff | auto
AGENT_BACKEND=opencode

# اختياري: مسار التنفيذي وملف الإعدادات
FREEBUFF_BIN=/home/ubuntu/.local/bin/freebuff
FREEBUFF_CONFIG_DIR=/home/ubuntu/.config/manicode

# مطلوب عملياً: مساحة العمل التي سيقرأ ويكتب فيها Freebuff
FREEBUFF_WORKSPACE=/home/ubuntu/github-workspaces

# اختياري: نموذج افتراضي وقائمة النماذج المسموحة (تُعرض في /backend)
FREEBUFF_MODEL=
FREEBUFF_MODELS=

# لا تكتب إعدادات Freebuff إلا بموافقة صريحة
FREEBUFF_MODEL_WRITE=0
FREEBUFF_TRUST_AGENTS=0

FREEBUFF_IDLE_SECONDS=25
FREEBUFF_STARTUP_SECONDS=90
FREEBUFF_TIMEOUT_SECONDS=3600
FREEBUFF_EXPORT_SECONDS=60
FREEBUFF_MIN_VERSION=0.0.140
FREEBUFF_MAX_OUTPUT_CHARS=120000
```

بعدها:

```bash
systemctl --user restart opencode-bridge-telegram.service
```

ثم من Telegram:

```text
/backend            # الحالة: الاختيار المحفوظ، المطبّق، إصدار Freebuff، الدخول
/backend freebuff   # لحسابك فقط
/backend auto       # Freebuff إن كان جاهزاً، وإلا OpenCode
/backend opencode   # رجوع فوري
```

`AGENT_BACKEND` يحدد الافتراضي للخادم، لكن تفضيل المستخدم المحفوظ في
`backend_preferences` يتقدّم عليه.

## 5) كيف تُختار الجلسة

- مفتاح جلسة Freebuff هو `(owner, "freebuff", workspace)`.
- أول مهمة تبدأ محادثة جديدة؛ بعد نجاحها يُحفظ معرّف المحادثة (اسم مجلد chat)
  في `backend_sessions`.
- المهمة التالية في **نفس مساحة العمل** تُستأنف عبر `--continue <id>`.
- تغيير `FREEBUFF_WORKSPACE` يبدأ سياقاً جديداً (مفتاح مختلف).
- `/new` يحذف ربط الجلسة ويبدأ محادثة جديدة، و`/abort` يقتل عملية Freebuff.

## 6) الأمان

- **لا يُقرأ `log.jsonl` أبداً.** قد يحتوي مفتاح الحساب بنص صريح عند أخطاء 401.
  يُقرأ فقط `chat-messages.json` مع تحقق من الحجم والشكل.
- بيئة الطفل محدودة: `HOME`, `PATH`, `LANG`, `TERM`, `XDG_*`,
  `DBUS_SESSION_BUS_ADDRESS`, `DISPLAY` فقط. لا
  `TELEGRAM_BOT_TOKEN` ولا مفاتيح المزودين ولا الوكيل.
- لا تُوضع التعليمات في `argv`؛ تُكتب في ملف `0600` ويُشار إليها بمسار.
- cwd محصور في `FREEBUFF_WORKSPACE`؛ لا shell ولا أوامر مركّبة.
- قتل مجموعة العملية (`SIGTERM` ثم `SIGKILL`) عند الإلغاء أو المهلة.
- تُنقّى المخرجات من ANSI وتُخفى `cb_…` و`sk-…` و`ghp_…` و`Bearer …` قبل
  Telegram، وتُحدّ بـ`FREEBUFF_MAX_OUTPUT_CHARS`.
- `FREEBUFF_TRUST_AGENTS=1` يمرّر `--trust-agents` ويجب أن يبقى مطفأً إلا في CI
  موثوق.
- `FREEBUFF_MODEL_WRITE=1` يسمح بتعديل `freebuffModel` في إعدادات Freebuff؛
  افتراضياً مطفأ.

## 7) الاختبارات والتحقق

```bash
cd /home/ubuntu/opencode-bridge
python -m unittest discover -s tests -p "test_*.py" -v
bash scripts/quality.sh
```

اختبارات PTY تُتخطّى تلقائياً على Windows. اختبار النهاية-إلى-النهاية يستخدم
TUI وهمي ولا يحتاج حساب Freebuff أو شبكة:

```bash
python -m unittest tests.freebuff.test_freebuff_pty -v
```

الاختبار اليدوي على الخادم:

1. `/backend freebuff` ثم مهمة نصية قصيرة.
2. تحقق من وصول الرد في نفس رسالة الحالة.
3. جرّب `/abort` أثناء مهمة طويلة.
4. أعد إرسال مهمة وتأكد من `--continue` (نفس المحادثة، لا سياق مكرر).
5. أعد تشغيل الخدمة وتأكد أن الربط محفوظ.

## 8) استكشاف الأخطاء

| عرض `/backend` | المعنى | الحل |
| --- | --- | --- |
| `reason: not_installed` | لم يُعثر على التنفيذي | ثبّت Freebuff أو اضبط `FREEBUFF_BIN` |
| `reason: version_too_old` | إصدار أقل من الحد | `npm install -g freebuff@latest` |
| `reason: version_unparseable` | مخرجات `--version` غير معروفة | حدّث Freebuff، وتأكد أنه التنفيذي الصحيح |
| `reason: probe_timeout` | `--version` تجاوز 15 ثانية | افحص الشبكة/التحميل الأول |
| `logged_in: لا` | لا يوجد `credentials.json` | `freebuff login` بمستخدم الخدمة |
| خطأ «تغيّرت الواجهة» | TUI غير معروف | ثبّت إصداراً مدعوماً أو ارجع إلى OpenCode |
| Rate limit / quota | حصة Freebuff | ينتظر البريدج ويعيد المحاولة تلقائياً |

سجلات الخدمة:

```bash
journalctl --user -u opencode-bridge-telegram.service -f
```

## 9) التراجع

الأسرع: `/backend opencode` في Telegram.

التراجع الشامل:

```dotenv
AGENT_BACKEND=opencode
```

ثم:

```bash
systemctl --user restart opencode-bridge-telegram.service
```

لا حاجة لأي migration عكسي: جداول `backend_sessions` و`backend_preferences`
إضافية فقط، وOpenCode يستخدم `sessions` القديم كما هو.

## 10) ما لا يدعمه التكامل حالياً

- مشاركة الجلسات (`/share` غير متاح لخلفية Freebuff).
- طلبات الإذن/الأسئلة التفاعلية (Freebuff لا يمرّرها كواجهة برمجية).
- ضبط النموذج تلقائياً ما لم يُفعّل `FREEBUFF_MODEL_WRITE=1`.
- ACP: جاهز معمارياً، ويُضاف كtransport جديد حين يُدمج upstream.

## 11) المراجع

- طلب headless upstream: <https://github.com/CodebuffAI/freebuff/issues/947>
- PR الخاص بـACP: <https://github.com/CodebuffAI/freebuff/pull/1611>
- قرار معماري: [ADR 0001](adr/0001-freebuff-cli-backend.md)
