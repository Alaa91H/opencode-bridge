"""نظام الإشعارات الذكية (Smart Notifications) - نظام إرسال إشعارات ذكية.

يوفر هذا النظام إرسال إشعارات ذكية عبر Telegram للمستخدمين حول:
- طلبات السحب الجديدة والمحتاجة لمراجعة
- القضايا الجديدة والمحتاجة للانتباه
- الإصدارات الجديدة المنشورة
- فشل المهام المجدولة
- تغييرات حالة الفروع المحمية
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from bridge.services.github.api import GitHubAPIClient

log = logging.getLogger("opencode_bridge.github.notifications")


class NotificationType(Enum):
    """أنواع الإشعارات."""

    PULL_REQUEST_OPENED = "pull_request.opened"
    PULL_REQUEST_READY_FOR_REVIEW = "pull_request.ready_for_review"
    PULL_REQUEST_MERGED = "pull_request.merged"
    PULL_REQUEST_CLOSED = "pull_request.closed"
    ISSUE_OPENED = "issue.opened"
    ISSUE_ASSIGNED = "issue.assigned"
    ISSUE_CLOSED = "issue.closed"
    RELEASE_PUBLISHED = "release.published"
    RELEASE_CREATED = "release.created"
    BUILD_FAILED = "build.failed"
    DEPLOYMENT_FAILED = "deployment.failed"
    TASK_FAILED = "task.failed"
    SECURITY_ALERT = "security.alert"
    CUSTOM = "custom"


class NotificationPriority(Enum):
    """أولويات الإشعارات."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class NotificationRule:
    """قاعدة إشعار تحدد متى يجب إرسال الإشعارات."""

    id: str
    name: str
    description: str
    notification_type: NotificationType
    owner: str
    repo: str
    is_enabled: bool = True
    notify_owners: bool = True
    notify_assignees: bool = True
    notify_reviewers: bool = True
    min_priority: NotificationPriority = NotificationPriority.LOW
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Notification:
    """إشعار واحد."""

    id: str
    type: NotificationType
    priority: NotificationPriority
    title: str
    message: str
    owner: str
    repo: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sent: bool = False
    sent_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SmartNotificationManager:
    """مدير الإشعارات الذكية للمشاريع على GitHub.

    يتيح إنشاء قواعد إشعار مخصصة وإرسال إشعارات ذكية عبر Telegram
    عند حدوث أحداث مهمة على GitHub.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client
        self._rules: dict[str, NotificationRule] = {}
        self._notifications: dict[str, Notification] = {}
        self._notification_handlers: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def create_rule(
        self,
        name: str,
        description: str,
        notification_type: NotificationType,
        owner: str,
        repo: str,
        notify_owners: bool = True,
        notify_assignees: bool = True,
        notify_reviewers: bool = True,
        min_priority: NotificationPriority = NotificationPriority.LOW,
    ) -> str:
        """إنشاء قاعدة إشعار جديدة.

        Args:
            name: اسم القاعدة
            description: وصف القاعدة
            notification_type: نوع الإشعار
            owner: مالك المستودع
            repo: اسم المستودع
            notify_owners: هل إرسال إشعارات لأصحاب المستودع
            notify_assignees: هل إرسال إشعارات للمكلفين بالمهام
            notify_reviewers: هل إرسال إشعارات للمراجعين
            min_priority: الحد الأدنى لأولوية الإشعار

        Returns:
            معرف القاعدة التي تم إنشاؤها
        """
        rule_id = str(uuid.uuid4())

        rule = NotificationRule(
            id=rule_id,
            name=name,
            description=description,
            notification_type=notification_type,
            owner=owner,
            repo=repo,
            notify_owners=notify_owners,
            notify_assignees=notify_assignees,
            notify_reviewers=notify_reviewers,
            min_priority=min_priority,
        )

        async with self._lock:
            self._rules[rule_id] = rule

        log.info(
            "تم إنشاء قاعدة إشعار جديدة: %s (المستودع: %s/%s, النوع: %s)",
            name,
            owner,
            repo,
            notification_type.value,
        )
        return rule_id

    async def get_rule(self, rule_id: str) -> NotificationRule | None:
        """الحصول على قاعدة إشعار بمعرفها."""
        async with self._lock:
            return self._rules.get(rule_id)

    async def list_rules(
        self,
        owner: str | None = None,
        repo: str | None = None,
        enabled_only: bool = False,
    ) -> list[NotificationRule]:
        """قواعد الإشعارات مع تصفية اختيارية."""
        async with self._lock:
            rules = list(self._rules.values())

            if owner:
                rules = [r for r in rules if r.owner == owner]
            if repo:
                rules = [r for r in rules if r.repo == repo]
            if enabled_only:
                rules = [r for r in rules if r.is_enabled]

            return rules

    async def update_rule(
        self,
        rule_id: str,
        **updates: Any,
    ) -> bool:
        """تحديث قاعدة إشعار."""
        async with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                return False

            for key, value in updates.items():
                if hasattr(rule, key):
                    setattr(rule, key, value)

            log.info("تم تحديث قاعدة الإشعار: %s", rule_id)
            return True

    async def delete_rule(self, rule_id: str) -> bool:
        """حذف قاعدة إشعار."""
        async with self._lock:
            if rule_id not in self._rules:
                return False

            del self._rules[rule_id]
            log.info("تم حذف قاعدة الإشعار: %s", rule_id)
            return True

    async def enable_rule(self, rule_id: str) -> bool:
        """تفعيل قاعدة إشعار."""
        return await self.update_rule(rule_id, is_enabled=True)

    async def disable_rule(self, rule_id: str) -> bool:
        """تعطيل قاعدة إشعار."""
        return await self.update_rule(rule_id, is_enabled=False)

    async def send_notification(
        self,
        type: NotificationType,
        priority: NotificationPriority,
        title: str,
        message: str,
        owner: str,
        repo: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """إرسال إشعار جديد.

        Args:
            type: نوع الإشعار
            priority: أولوية الإشعار
            title: عنوان الإشعار
            message: نص الإشعار
            owner: مالك المستودع
            repo: اسم المستودع
            metadata: بيانات إضافية

        Returns:
            معرف الإشعار الذي تم إنشاؤه
        """
        notification_id = str(uuid.uuid4())

        notification = Notification(
            id=notification_id,
            type=type,
            priority=priority,
            title=title,
            message=message,
            owner=owner,
            repo=repo,
            metadata=metadata or {},
        )

        async with self._lock:
            self._notifications[notification_id] = notification

        # محاولة الإرسال فوراً
        await self._send_notification(notification)

        log.info(
            "تم إرسال إشعار: %s (النوع: %s، الأولوية: %s)",
            notification_id,
            type.value,
            priority.value,
        )
        return notification_id

    async def _send_notification(self, notification: Notification) -> None:
        """إرسال إشعار فعلياً."""
        try:
            if notification.type in self._notification_handlers:
                handler = self._notification_handlers[notification.type]
                if asyncio.iscoroutinefunction(handler):
                    await handler(notification)
                else:
                    handler(notification)

            notification.sent = True
            notification.sent_at = datetime.now(UTC)
            log.info("تم إرسال الإشعار بنجاح: %s", notification.id)
        except Exception as exc:
            notification.sent = False
            notification.error = str(exc)
            log.error("فشل في إرسال الإشعار %s: %s", notification.id, exc)

    def register_notification_handler(
        self,
        notification_type: NotificationType,
        handler: Any,
    ) -> None:
        """تسجيل معالج لنوع إشعار معين."""
        self._notification_handlers[notification_type] = handler
        log.info("تم تسجيل معالج إشعار: %s", notification_type.value)

    async def get_notification(self, notification_id: str) -> Notification | None:
        """الحصول على إشعار بمعرفه."""
        async with self._lock:
            return self._notifications.get(notification_id)

    async def list_notifications(
        self,
        owner: str | None = None,
        repo: str | None = None,
        sent_only: bool = False,
        unsent_only: bool = False,
        limit: int = 100,
    ) -> list[Notification]:
        """قائمة الإشعارات مع تصفية اختيارية."""
        async with self._lock:
            notifications = list(self._notifications.values())

            if owner:
                notifications = [n for n in notifications if n.owner == owner]
            if repo:
                notifications = [n for n in notifications if n.repo == repo]
            if sent_only:
                notifications = [n for n in notifications if n.sent]
            if unsent_only:
                notifications = [n for n in notifications if not n.sent]

            notifications.sort(key=lambda n: n.created_at, reverse=True)
            return notifications[:limit]

    async def get_notification_statistics(self) -> dict[str, Any]:
        """الحصول على إحصائيات الإشعارات."""
        async with self._lock:
            total = len(self._notifications)
            sent = sum(1 for n in self._notifications.values() if n.sent)
            unsent = sum(1 for n in self._notifications.values() if not n.sent)
            failed = sum(1 for n in self._notifications.values() if n.error)

            by_type: dict[str, int] = {}
            for n in self._notifications.values():
                by_type[n.type.value] = by_type.get(n.type.value, 0) + 1

            by_priority: dict[str, int] = {}
            for n in self._notifications.values():
                by_priority[n.priority.value] = by_priority.get(n.priority.value, 0) + 1

            return {
                "total_notifications": total,
                "sent": sent,
                "unsent": unsent,
                "failed": failed,
                "by_type": by_type,
                "by_priority": by_priority,
                "total_rules": len(self._rules),
                "enabled_rules": sum(1 for r in self._rules.values() if r.is_enabled),
            }

    async def format_notification_for_telegram(self, notification: Notification) -> str:
        """تنسيق إشعار للعرض في Telegram."""
        type_icons = {
            NotificationType.PULL_REQUEST_OPENED: "🔀",
            NotificationType.PULL_REQUEST_READY_FOR_REVIEW: "👀",
            NotificationType.PULL_REQUEST_MERGED: "✅",
            NotificationType.PULL_REQUEST_CLOSED: "❌",
            NotificationType.ISSUE_OPENED: "🐛",
            NotificationType.ISSUE_ASSIGNED: "👤",
            NotificationType.ISSUE_CLOSED: "✅",
            NotificationType.RELEASE_PUBLISHED: "🏷️",
            NotificationType.RELEASE_CREATED: "📦",
            NotificationType.BUILD_FAILED: "🚨",
            NotificationType.DEPLOYMENT_FAILED: "🚨",
            NotificationType.TASK_FAILED: "🚨",
            NotificationType.SECURITY_ALERT: "🔒",
            NotificationType.CUSTOM: "📢",
        }

        priority_icons = {
            NotificationPriority.LOW: "🔵",
            NotificationPriority.MEDIUM: "🟡",
            NotificationPriority.HIGH: "🟠",
            NotificationPriority.CRITICAL: "🔴",
        }

        icon = type_icons.get(notification.type, "📢")
        priority_icon = priority_icons.get(notification.priority, "🔵")

        text = (
            f"{icon} **إشعار {notification.priority.value.upper()}**\n\n"
            f"{priority_icon} **العنوان:** {notification.title}\n"
            f"📝 **الرسالة:** {notification.message}\n"
            f"📦 **المستودع:** {notification.owner}/{notification.repo}\n"
            f"📅 **الوقت:** {notification.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        )

        if notification.metadata:
            text += "\n📋 **تفاصيل إضافية:**\n"
            for key, value in notification.metadata.items():
                text += f"   • {key}: {value}\n"

        return text

    async def format_notifications_summary_for_telegram(
        self,
        notifications: list[Notification],
    ) -> str:
        """تنسيق ملخص الإشعارات للعرض في Telegram."""
        if not notifications:
            return "📭 لا توجد إشعارات جديدة"

        text = "📢 **الإشعارات الأخيرة**\n\n"

        for notification in notifications[:10]:
            icon = {
                NotificationType.PULL_REQUEST_OPENED: "🔀",
                NotificationType.PULL_REQUEST_MERGED: "✅",
                NotificationType.ISSUE_OPENED: "🐛",
                NotificationType.RELEASE_PUBLISHED: "🏷️",
                NotificationType.BUILD_FAILED: "🚨",
                NotificationType.SECURITY_ALERT: "🔒",
            }.get(notification.type, "📢")

            status = "✅ تم الإرسال" if notification.sent else "⏳ قيد الإرسال"

            text += (
                f"{icon} **{notification.title}**\n"
                f"   {notification.message[:80]}...\n"
                f"   {notification.owner}/{notification.repo} | {notification.created_at.strftime('%H:%M')}\n"
                f"   الحالة: {status}\n\n"
            )

        return text
