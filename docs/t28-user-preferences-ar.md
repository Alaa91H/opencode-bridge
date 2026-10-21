# T28 — User Preferences

UserPreferences يجمع القيم المطلوبة: timezone، language، notification level، default workspace، default execution profile، model preference، output style، retention، وschedule defaults.

الـtimezone يتحقق منه عبر IANA ZoneInfo، كما تتحقق notification/profile/output/retention من القيم غير الصالحة. القيمة الافتراضية آمنة: UTC، SAFE، normal notifications، summary output، retention 30 يومًا.

UserSettingsStore يستخدم جدول user_settings الموجود منذ Database v2 ويحفظ JSON owner-scoped بمعاملة SQLite وupsert. لا يوجد SQL في Telegram handlers. UserPreferencesService هو application boundary للقراءة والتعديل ويتحقق من المفاتيح غير المعروفة قبل الحفظ.

الاختبارات تغطي الحقول التسعة، جميعها في update واحد، owner isolation، restart persistence، timezone/retention validation، ورفض المفاتيح غير المعروفة.
