"""لوحة تحكم المشاريع (Project Dashboard).

توفر لوحات تحكم تفاعلية لعرض حالة المستودعات، طلبات السحب،
القضايا، الإصدارات، والفروع في واجهة موحدة.
"""

from bridge.services.github.dashboard.dashboard import (
    DashboardFormatter,
    DashboardSummary,
    ProjectDashboard,
    RepositoryDashboard,
)

__all__ = [
    "DashboardFormatter",
    "DashboardSummary",
    "ProjectDashboard",
    "RepositoryDashboard",
]
