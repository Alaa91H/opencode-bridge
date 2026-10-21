# T26 — نتائج طويلة بلا قص

LongOutputComposer يفصل تمثيل النتيجة الكاملة عن قيود Telegram. المحتوى الأصلي يُحوّل كاملًا إلى UTF-8 payload ويحصل على OutputArtifact يحوي الاسم، MIME، الحجم وSHA-256. OutputManifest يربط artifact بالـtask والsummary والصفحات الاختيارية.

summary مخصص للرسالة فقط ولا يُستخدم بدل النتيجة الأصلية. OutputService يمرر payload الكامل إلى storage callback قبل التسليم، وبذلك لا يؤدي حد الرسالة إلى فقد البيانات.

عند paginate=true تُنشأ صفحات للعرض التفاعلي ويمكن جمعها لإعادة النص كاملًا؛ artifact الكامل يبقى المصدر النهائي دائمًا. يدعم .md و.txt.

اختبارات T26 تستخدم نتيجة ضخمة متعددة اللغات وتثبت byte-exact round trip، checksum/size، إعادة تركيب pagination، اكتمال النتائج القصيرة، وأن storage يستقبل الأصل الكامل لا summary.
