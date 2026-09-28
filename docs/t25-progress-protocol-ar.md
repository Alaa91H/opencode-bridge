# T25 — Progress Protocol موحد

ProgressEvent typed ويغطي queued/downloading/analyzing/planning/executing/waiting_model/processing_media/uploading/completed. percent اختياري ومتحقق منه، والحدث يحمل task_id/detail/timestamp.

ProgressRenderer منفصل عن domain protocol حتى يمكن تغيير واجهة Telegram دون تغيير semantics الأحداث.

ProgressService يحفظ كل event أولًا عبر persist callback، حتى عندما يمنع debounce إرسال update إلى Telegram. min_interval يطبق per-task، بينما force يسمح للحالات النهائية بالتجاوز. كل task مرتبط بـmessage_id واحد ويتم تعديل الرسالة نفسها بدل إنشاء سلسلة رسائل.

بعد restart يمكن استعادة message binding من التخزين عبر restore_message_binding ثم متابعة تعديل الرسالة ذاتها. اختبارات T25 تثبت قائمة الحالات، validation/rendering، persistence أثناء debounce، one-message principle، وإعادة الربط.
