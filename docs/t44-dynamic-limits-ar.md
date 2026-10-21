# T44 — إزالة الحدود الداخلية القديمة

DynamicLimits يحول attachment count/total bytes/pending seconds/workers/output count إلى policy values. القيمة 0 تعني unlimited by application policy، وauto تعني حساب الحد من ResourceBudget الحالي.

auto workers يعتمد CPU/RAM، attachment bytes يعتمد disk/RAM، وattachment count يتدرج مع RAM. هذا لا يلغي Resource Guard في T22؛ application unlimited لا يعني تجاوز OOM/disk safety.

القيم الرقمية القديمة تبقى مدعومة كما هي، ما يوفر migration compatibility للمتغيرات/الإعدادات التي كانت تعطي أرقامًا ثابتة. القيم السالبة أو غير auto تُرفض.

اختبارات T44 تغطي unlimited=0، auto resource calculation، finite legacy compatibility، وresource guard على بيئة صغيرة.
