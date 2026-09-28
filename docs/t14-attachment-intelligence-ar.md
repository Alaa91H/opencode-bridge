# T14 — Attachment Intelligence Router

الهدف هو عدم إجبار OpenCode/model context على استقبال الملف الخام عندما تكون representation أصغر وأكثر صلة.

- small supported: direct attachment حتى max_direct_bytes.
- large text: chunks + index + context-aware selected chunks.
- PDF: text/pages/images/selected chunks من Media Pipeline.
- video: selected frames + transcript + metadata.
- audio: transcript + timestamps + metadata.
- archive: manifest أولًا ثم selected files فقط.
- unsupported binary: metadata فقط بدل تمرير binary عشوائي.

الاختيار النصي الحالي deterministic ويرتب chunks حسب كلمات query مع fallback لأول chunks. هذه طبقة selection مستقلة يمكن تحسين scoring فيها لاحقًا ضمن Model/Context stages دون تغيير عقد T14.
