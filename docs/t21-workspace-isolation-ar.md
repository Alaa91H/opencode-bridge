# T21 — Workspace Isolation

كل development task يحصل على path مستقل تحت workspace root. الاستراتيجية الافتراضية Git worktree detached؛ ويمكن اختيار temp clone بلا hardlinks. لا يتم تشغيل مهمتين بنفس task/repository lock في الوقت نفسه.

WorkspacePolicy تفصل صلاحيات commit/push/PR. commit مسموح افتراضيًا داخل workspace، بينما push وPR مرفوضان افتراضيًا حتى تمنحهما policy أعلى. لا توجد force-push semantics.

عند النجاح يزيل manager الـworktree أو clone الخاص بالمهمة فقط. عند الفشل يمكن retain_failed إبقاء المساحة للتشخيص. cleanup لا يحذف repository الأصلية.

اختبارات T21 تغطي lock، paths مستقلة، failure retention، deny-by-default للpush/PR، وclone cleanup الذي يحافظ على source repository.
