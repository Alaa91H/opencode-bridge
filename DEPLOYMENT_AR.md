# نشر بوت OpenCode العربي دون بناء على الخادم

تحتوي هذه الحزمة على كود Python جاهز وإعدادات خدمات OpenCode. يجب إجراء التحقق والتجهيز في بيئة التطوير، ثم نقل الملفات الجاهزة فقط إلى الخادم. **لا تشغّل أوامر البناء أو التجميع أو تثبيت الحزم على الخادم.**

## متطلبات الخادم

يتطلب التشغيل بيئة Python افتراضية جاهزة في `/home/ubuntu/opencode-bridge/venv` وبها الإصدارات المحددة في `requirements.txt`، وتثبيت OpenCode في `/home/ubuntu/.opencode/bin/opencode`. لا يغيّر هذا الإصدار البيئة الافتراضية أو يثبت أي اعتماديات على الخادم.

ينبغي أن يظل ملف `/home/ubuntu/opencode-bridge/.env` محليًا على الخادم وبصلاحية `600`. لا تضمّنه في أرشيفات أو مستودعات. أضف أو عدّل المتغيرات الآتية، مع استخدام القيمة العشوائية ذاتها لكلمتي المرور:

```dotenv
OPENCODE_SERVER_USERNAME=opencode
OPENCODE_SERVER_PASSWORD=كلمة_مرور_عشوائية_طويلة
OPENCODE_PASSWORD=كلمة_مرور_عشوائية_طويلة
OPENCODE_AGENT=telegram-operator
# عند حجب api.telegram.org فقط:
TELEGRAM_PROXY_URL=socks5://host:port
```

## ترتيب النشر

أوقف خدمات البوت، ثم انسخ الملفات الجاهزة فقط، ثم ثبّت وحدات `systemd` من مجلد `deploy/`، وأعد تحميل مدير الخدمات وأعد التشغيل. شغّل الفحوص التالية بعد النشر:

```bash
systemctl --user status opencode-serve.service opencode-bridge-telegram.service --no-pager
curl --fail --silent --user "opencode:$OPENCODE_SERVER_PASSWORD" http://127.0.0.1:4096/global/health
journalctl --user -u opencode-bridge-telegram.service -n 50 --no-pager
```

## الصيانة اليومية والتحديث الذاتي

ثبّت خدمة الصيانة ذات صلاحية root مرة واحدة من النسخة الموثوقة للمستودع:

```bash
cd /home/ubuntu/opencode-bridge
sudo bash maintenance/install-root-assets.sh
systemctl status opencode-bridge-maintenance.timer --no-pager
```

المؤقت يعمل يوميًا عند 08:30 UTC مع تأخير عشوائي يصل إلى 20 دقيقة. الدورة اليومية تنفذ تحديثات APT الآمنة والتنظيف، ثم تفحص `origin/main` للمستودع `Alaa91H/opencode-bridge`. لا يطبّق التحديث إذا كان الفرع المحلي متسخًا أو متشعبًا. عند وجود تحديث fast-forward، تُختبر النسخة الجديدة أولًا داخل Git worktree مؤقت عبر Python syntax واختبارات الوحدة وJSON وshell syntax، ثم تُطبّق فقط بعد نجاح التحقق وتُعاد مزامنة وحدات systemd وإعادة تشغيل خدمات الجسر.

بعد ذلك تُشغّل مهمة OpenCode مستقلة كمستخدم `ubuntu`: تعيد Agent Scout تقييم نماذج Zen ذات التكلفة الصفرية، تختار الأقوى للتطوير الوكيلي، تستخدم أعلى reasoning variant معلن في الكتالوج عندما يكون متاحًا، ثم تنفذ تدقيقًا يوميًا للسلامة وتكتب التقرير داخل `runtime/agent-maintenance-latest.md`.

يمكن تعطيل الاختيار التلقائي للأقوى فقط عند الحاجة بوضع `AGENT_SCOUT_AUTO_STRONGEST=0`. القيمة الافتراضية هي `1`، وهي تتغلب على إعداد pin قديم حتى لا يبقى الخادم مثبتًا على نموذج أضعف.

## الضوابط

إذا كانت شبكة الخادم تحجب `api.telegram.org`، حدّد `TELEGRAM_PROXY_URL` بعنوان وكيل HTTP(S) أو SOCKS موثوق، ثم أعد تشغيل خدمة البوت. لا تسجل بيانات اعتماد الوكيل أو تشاركها في المحادثات.

تمنع سياسة `opencode.json` أوامر البناء والتجميع وتثبيت الحزم على الخادم في طبقة وكيل OpenCode، كما يرفض البوت الطلبات الصريحة لهذه العمليات قبل إرسالها للوكيل. تظل المهام التشغيلية الأخرى، مثل قراءة وتعديل الملفات المسموح بها وتشغيل الخدمات واستعمال أدوات الويب، متاحة لوكيل `telegram-operator`.

> يعمل خادم OpenCode على `127.0.0.1` فقط، ويُحمى بالمصادقة الأساسية. يقرأ الجسر كلمة المرور من `.env` ولا يعرضها للمستخدم أو في السجلات.
