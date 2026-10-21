# T13 — Media Processing Pipeline

المسار المنطقي: input → detect → validate → normalize → extract → segment → agent → compose.

## الأدوات الاختيارية

يكتشف النظام فقط الأدوات الموجودة مسبقًا عبر PATH: ffmpeg وffprobe وtesseract وpdftotext وpdfimages. لا توجد أي محاولة لتثبيت dependency أو تعديل host.

## الصور

ImageProcessor يدعم metadata، ويعلن قدرات normalization/tiling عند توفر ffmpeg، وOCR عند توفر Tesseract، ويمكن تشغيل pipeline على عدة MediaInput بشكل مستقل (multi-image).

## PDF

PdfProcessor يستخرج النص عند توفر pdftotext مع layout-preserving mode ثم يقسمه إلى chunks محدودة. يعلن توفر استخراج الصور عبر pdfimages، ويحتفظ بإشارة page/layout processing لاستخدامها في T14 router.

## الفيديو

VideoProcessor يستخدم ffprobe للstreams/format metadata. عند توفر ffmpeg يعلن قدرات audio extraction وkeyframes وscene detection وframe extraction. transcript لا يُدعى متاحًا ما لم يوجد STT backend فعلي.

## الصوت

AudioProcessor يعلن normalize/segment عند توفر ffmpeg. STT/timestamps/speaker segmentation تبقى false عندما لا يوجد backend فعلي بدل اختلاق قدرة غير موجودة.

## الأرشيف

ZIP يُفحص manifest-first. يمنع absolute/traversal/drive-like paths ويطبق max files/max expanded bytes/max compression ratio قبل extraction. الاستخراج يتم بمسار محصور تحت destination.

## degraded mode

غياب الأدوات الاختيارية لا يكسر تحليل النوع؛ يعود processor بmetadata والقدرات المتاحة. هذا يسمح لـT14 باختيار representation آمن ومناسب للسياق.
