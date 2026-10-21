# T48 — Migration من v1.8 إلى 2.0

## القاعدة

لا يبدأ أي فحص/تحويل قبل SQLite online backup. `V18MigrationPlanner.inspect(dry_run=True)` يقرأ legacy schema دون تعديل ويصدر MigrationReport لعدد sessions وschedules وpending attachments وowner/user IDs.

## البيانات

- sessions: تُحصر حسب telegram_user_id مع الحفاظ على owner identity.
- schedules: لكل legacy row مفتاح migration ثابت SHA-256 من owner + legacy id. هذا المفتاح هو أساس منع تحويل نفس schedule مرتين، وتبقى occurrence idempotency في T06/T07 مانعة للتنفيذ المزدوج.
- pending attachments: يُقرأ JSON ويُحسب المحتوى قبل التحويل إلى storage/refs v2.
- user IDs: تُجمع من sessions/schedules/pending batches وتظهر في التقرير للمقارنة قبل/بعد.

## Dry-run والتقرير

التشغيل الأول دائمًا dry-run. التقرير قابل للتحويل إلى dict/JSON ويجب حفظه مع artifact الترقية. أي schema غير متوقع أو duplicate migration key يوقف العملية.

## Rollback

النسخة المأخوذة قبل migration هي rollback source. اختبار T48 يعدل المصدر بعد backup ثم يثبت أن بيانات sessions الأصلية ما زالت قابلة للاستعادة من backup.

## Legacy config

بعد نجاح migration وعدم وجود production reference إلى `opencode-v3.json` يصبح `opencode.json` المصدر الوحيد. حذف compatibility artifact يتم فقط عندما تؤكد بيئة deployment أنها لم تعد تشير إليه.
