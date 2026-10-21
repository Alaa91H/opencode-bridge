"""نظام الإشعارات الذكية (Smart Notifications).

يوفر نظام إرسال إشعارات ذكية عبر Telegram للمستخدمين حول:
- طلبات السحب الجديدة والمحتاجة لمراجعة
- القضايا الجديدة والمحتاجة للانتباه
- الإصدارات الجديدة المنشورة
- فشل المهام المجدولة
- تغييرات حالة الفروع المحمية
"""

from bridge.services.github.notifications.notification_manager import (
    Notification,
    NotificationPriority,
    NotificationRule,
    NotificationType,
    SmartNotificationManager,
)

__all__ = [
    "Notification",
    "NotificationPriority",
    "NotificationRule",
    "NotificationType",
    "SmartNotificationManager",
]
