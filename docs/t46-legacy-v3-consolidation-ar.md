# T46 — إزالة ازدواجية Legacy/V3

production configuration canonical هو opencode.json فقط؛ CI لم يعد يعامل opencode-v3.json كمسار production ثانٍ. الملفان كانا متطابقين byte-for-byte في المحتوى الدلالي، لذلك لا توجد ميزة V3 منفصلة يجب الحفاظ عليها.

BridgeSettings هو config system الوحيد من T03، Durable Queue هو task engine، وOpenCode Client v2 هو client contract. مسارات v2/v3 في أسماء الاختبارات/compatibility لا تعني engines إنتاجية متعددة.

Migration path: أي deployment يشير إلى opencode-v3.json يجب تحويله إلى opencode.json قبل حذف الملف legacy. لهذا لا يُحذف opencode-v3.json فورًا في T46؛ يبقى compatibility artifact غير مُتحقق كproduction config حتى تثبت T48 migration عدم وجود references، ثم يمكن حذفه بأمان.

هذا يحقق production path/config/task engine/client واحد مع حذف legacy مشروط بإثبات عدم الاستخدام، بدل حذف متسرع يكسر deployment قديم.
