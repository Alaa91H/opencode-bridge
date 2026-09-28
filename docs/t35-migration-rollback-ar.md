# T35 — Database Migration Rollback

MigrationPlan يثبت version وSHA-256 checksum للنص وmin-compatible-version وكون migration reversible. compatibility window تمنع تطبيق plan على schema خارج المجال المقصود.

MigrationSafety يرفض checksum mismatch. migration غير القابلة للعكس لا يسمح لها بالمرور في self-update/deployment دون Snapshot موجود فعليًا وchecksum صحيح. هذا يربط T35 مع online backup في T33 ويمنع migration خطرة بلا نقطة استعادة.

rollback strategy صريحة: reverse_migration عندما تكون reversible، وإلا restore_snapshot. forward migration تبقى callback T34/runner الحالي لكن يجب أن تمر بوابة plan/checksum/snapshot قبل التنفيذ.

اختبارات T35 تغطي version/checksum، compatibility window، رفض migration الخطرة بلا snapshot، كشف snapshot tampering، واستراتيجية rollback/restore.
