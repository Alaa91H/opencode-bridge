# T37 — Secret Management

SecretSource يفصل استرجاع السر عن config/domain. SystemdCredentialSource يدعم systemd credentials اختياريًا من CREDENTIALS_DIRECTORY، وChainedSecretSource يسمح بأولوية credentials ثم EnvironmentSecretSource كمسار compatibility.

هذا يتيح migration تدريجية من .env دون كسر deployment الحالي: ضع credential بالاسم نفسه، فيتقدم على env؛ وعند اكتمال النقل يمكن إزالة env secret دون تغيير callers.

exposed_secret يوفر scoped access ويزيل المرجع المحلي عند الخروج لتقليل مدة exposure الممكنة داخل التطبيق. لا تسجل هذه الطبقة القيم ولا تضيفها إلى structured logs.

encrypted local store/secrets manager يمكن تنفيذه كSecretSource إضافي عندما تحتاج البيئة ذلك؛ لا يتم اختراع تشفير محلي بمفتاح محفوظ بجانب ciphertext لأنه لا يضيف حماية حقيقية.

اختبارات T37 تغطي systemd credentials، precedence، backward-compatible env migration، وmissing/scoped secret behavior.
