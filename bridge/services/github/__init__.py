"""نظام إدارة مشاريع GitHub الشامل لـ OpenCode Bridge.

يوفر تكاملاً شاملاً مع GitHub REST API v3 بما في ذلك:
- إدارة المستودعات (إنشاء، استنساخ، تكوين، حذف)
- إدارة طلبات السحب (إنشاء، مراجعة، دمج، إغلاق)
- إدارة القضايا (إنشاء، تسمية، تخصيص، إغلاق، إعادة فتح)
- إدارة الإصدارات (إنشاء، نشر، رفع أصول)
- إدارة الفروع (إنشاء، حذف، حماية)
- إدارة الأسرار ومفاتيح النشر
- لوحة تحكم المشاريع (عرض شامل لحالة جميع المشاريع)
- سير العمل التلقائي (تنفيذ مهام متكررة تلقائياً)
- الإشعارات الذكية (إشعارات فورية للأحداث المهمة)

تستخدم جميع الخدمات مصادقة GitHub API v3 مع معالجة حدود المعدل،
منطق المحاولة مجدداً، ورسائل أخطاء بالعربية لمستخدمي Telegram.
"""

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubConfig,
    GitHubNotFoundError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubResponse,
)
from bridge.services.github.branches import (
    GitHubBranch,
    GitHubBranchManager,
    GitHubBranchProtection,
)
from bridge.services.github.commands import GitHubCommandHandler
from bridge.services.github.dashboard import (
    DashboardFormatter,
    DashboardSummary,
    ProjectDashboard,
    RepositoryDashboard,
)
from bridge.services.github.issues import (
    GitHubIssue,
    GitHubIssueComment,
    GitHubIssueManager,
)
from bridge.services.github.notifications import (
    Notification,
    NotificationPriority,
    NotificationRule,
    NotificationType,
    SmartNotificationManager,
)
from bridge.services.github.pull_requests import (
    GitHubPRComment,
    GitHubPRReview,
    GitHubPullRequest,
    GitHubPullRequestManager,
)
from bridge.services.github.releases import (
    GitHubRelease,
    GitHubReleaseAsset,
    GitHubReleaseManager,
)
from bridge.services.github.repositories import (
    GitHubRepoConfig,
    GitHubRepoInfo,
    GitHubRepositoryManager,
    LocalRepositoryManager,
)
from bridge.services.github.secrets import (
    GitHubDeployKey,
    GitHubSecret,
    GitHubSecretManager,
)

__all__ = [
    "DashboardFormatter",
    "DashboardSummary",
    "GitHubAPIClient",
    "GitHubAPIError",
    "GitHubAuthenticationError",
    "GitHubBranch",
    "GitHubBranchManager",
    "GitHubBranchProtection",
    "GitHubCommandHandler",
    "GitHubConfig",
    "GitHubDeployKey",
    "GitHubIssue",
    "GitHubIssueComment",
    "GitHubIssueManager",
    "GitHubNotFoundError",
    "GitHubPRComment",
    "GitHubPRReview",
    "GitHubPermissionError",
    "GitHubPullRequest",
    "GitHubPullRequestManager",
    "GitHubRateLimitError",
    "GitHubRelease",
    "GitHubReleaseAsset",
    "GitHubReleaseManager",
    "GitHubRepoConfig",
    "GitHubRepoInfo",
    "GitHubRepositoryManager",
    "GitHubResponse",
    "GitHubSecret",
    "GitHubSecretManager",
    "LocalRepositoryManager",
    "Notification",
    "NotificationPriority",
    "NotificationRule",
    "NotificationType",
    "ProjectDashboard",
    "RepositoryDashboard",
    "SmartNotificationManager",
]

