"""لوحة تحكم المشاريع (Project Dashboard) - عرض شامل لحالة 모든 المشاريع.

توفر هذه الوحدة لوحات تحكم تفاعلية لعرض حالة المستودعات، طلبات السحب،
القضايا، الإصدارات، والفروع في واجهة موحدة يمكن الوصول منها عبر Telegram.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bridge.services.github.api import GitHubAPIClient
from bridge.services.github.branches import GitHubBranchManager
from bridge.services.github.issues import GitHubIssueManager
from bridge.services.github.pull_requests import GitHubPullRequestManager
from bridge.services.github.releases import GitHubReleaseManager
from bridge.services.github.repositories import GitHubRepositoryManager

log = logging.getLogger("opencode_bridge.github.dashboard")


@dataclass
class DashboardSummary:
    """ملخص لوحة التحكم للمشاريع."""

    total_repositories: int = 0
    active_repositories: int = 0
    total_pull_requests: int = 0
    open_pull_requests: int = 0
    total_issues: int = 0
    open_issues: int = 0
    total_releases: int = 0
    published_releases: int = 0
    total_branches: int = 0
    protected_branches: int = 0
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class RepositoryDashboard:
    """لوحة تحكم لمستودع واحد."""

    owner: str
    name: str
    full_name: str
    description: str | None
    private: bool
    default_branch: str
    stars: int
    forks: int
    watchers: int
    open_issues_count: int
    open_prs_count: int
    last_push: str | None
    last_activity: str | None
    health_status: str  # healthy, warning, critical
    recent_activity: list[dict] = field(default_factory=list)


class ProjectDashboard:
    """لوحة تحكم المشاريع - عرض شامل لحالة جميع المشاريع المتصلة."""

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client
        self.repo_manager = GitHubRepositoryManager(client)
        self.pr_manager = GitHubPullRequestManager(client)
        self.issue_manager = GitHubIssueManager(client)
        self.release_manager = GitHubReleaseManager(client)
        self.branch_manager = GitHubBranchManager(client)

    async def get_global_dashboard(self) -> DashboardSummary:
        """الحصول على ملخص عام لجميع المشاريع."""
        try:
            repos = await self.repo_manager.list_repositories()
            total_repos = len(repos)
            active_repos = sum(1 for r in repos if r.pushed_at is not None)

            # جمع الإحصائيات من جميع المستودعات
            total_prs = 0
            open_prs = 0
            total_issues = 0
            open_issues_count = 0
            total_releases = 0
            published_releases = 0
            total_branches = 0
            protected_branches = 0

            for repo in repos[:20]:  # حد أقصى 20 مستودع للأداء
                try:
                    prs = await self.pr_manager.list_pull_requests(
                        repo.owner, repo.name, per_page=100
                    )
                    total_prs += len(prs)
                    open_prs += sum(1 for p in prs if p.state == "open")

                    issues = await self.issue_manager.list_issues(
                        repo.owner, repo.name, per_page=100
                    )
                    total_issues += len(issues)
                    open_issues_count += sum(1 for i in issues if i.state == "open")

                    releases = await self.release_manager.list_releases(
                        repo.owner, repo.name, per_page=100
                    )
                    total_releases += len(releases)
                    published_releases += sum(
                        1 for r in releases if r.state == "released"
                    )

                    branches = await self.branch_manager.list_branches(
                        repo.owner, repo.name, per_page=100
                    )
                    total_branches += len(branches)
                    protected_branches += sum(1 for b in branches if b.protected)
                except Exception as exc:
                    log.warning(
                        "فشل في جلب بيانات المستودع %s/%s: %s",
                        repo.owner,
                        repo.name,
                        exc,
                    )

            return DashboardSummary(
                total_repositories=total_repos,
                active_repositories=active_repos,
                total_pull_requests=total_prs,
                open_pull_requests=open_prs,
                total_issues=total_issues,
                open_issues=open_issues_count,
                total_releases=total_releases,
                published_releases=published_releases,
                total_branches=total_branches,
                protected_branches=protected_branches,
            )
        except Exception as exc:
            log.error("فشل في جلب لوحة التحكم العامة: %s", exc)
            return DashboardSummary()

    async def get_repository_dashboard(
        self, owner: str, repo: str
    ) -> RepositoryDashboard | None:
        """الحصول على لوحة تحكم لمستودع محدد."""
        try:
            repo_info = await self.repo_manager.get_repository(owner, repo)

            prs = await self.pr_manager.list_pull_requests(
                owner, repo, per_page=100
            )
            issues = await self.issue_manager.list_issues(
                owner, repo, per_page=100
            )
            await self.release_manager.list_releases(
                owner, repo, per_page=100
            )
            await self.branch_manager.list_branches(
                owner, repo, per_page=100
            )

            # تحديد حالة الصحة
            health = "healthy"
            if repo_info.open_issues_count > 50 or len([p for p in prs if p.state == "open"]) > 20:
                health = "warning"
            if repo_info.open_issues_count > 200 or len([p for p in prs if p.state == "open"]) > 100:
                health = "critical"

            # الأنشطة الحديثة
            recent_activity = []
            for pr in prs[:5]:
                recent_activity.append({
                    "type": "pull_request",
                    "title": pr.title,
                    "number": pr.number,
                    "state": pr.state,
                    "created_at": pr.created_at,
                    "author": pr.author,
                })
            for issue in issues[:5]:
                recent_activity.append({
                    "type": "issue",
                    "title": issue.title,
                    "number": issue.number,
                    "state": issue.state,
                    "created_at": issue.created_at,
                    "author": issue.author,
                })

            recent_activity.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            recent_activity = recent_activity[:10]

            return RepositoryDashboard(
                owner=owner,
                name=repo,
                full_name=repo_info.full_name,
                description=repo_info.description,
                private=repo_info.private,
                default_branch=repo_info.default_branch,
                stars=repo_info.stargazers_count,
                forks=repo_info.forks_count,
                watchers=repo_info.watchers_count,
                open_issues_count=repo_info.open_issues_count,
                open_prs_count=len([p for p in prs if p.state == "open"]),
                last_push=repo_info.pushed_at,
                last_activity=repo_info.updated_at,
                health_status=health,
                recent_activity=recent_activity,
            )
        except Exception as exc:
            log.error("فشل في جلب لوحة التحكم للمستودع %s/%s: %s", owner, repo, exc)
            return None

    async def get_dashboard_summary_text(self) -> str:
        """الحصول على ملخص نصي للوحة التحكم للعرض في Telegram."""
        summary = await self.get_global_dashboard()

        text = (
            "📊 **لوحة تحكم المشاريع - GitHub**\n\n"
            f"📦 **المستودعات:** {summary.total_repositories} إجمالي، {summary.active_repositories} نشط\n"
            f"🔀 **طلبات السحب:** {summary.total_pull_requests} إجمالي، {summary.open_pull_requests} مفتوح\n"
            f"🐛 **القضايا:** {summary.total_issues} إجمالي، {summary.open_issues} مفتوح\n"
            f"🏷️ **الإصدارات:** {summary.total_releases} إجمالي، {summary.published_releases} منشور\n"
            f"🌿 **الفروع:** {summary.total_branches} إجمالي، {summary.protected_branches} محمي\n\n"
            f"🕐 آخر تحديث: {summary.last_updated.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return text

    async def get_repository_dashboard_text(
        self, owner: str, repo: str
    ) -> str:
        """الحصول على نص لوحة تحكم لمستودع محدد للعرض في Telegram."""
        dashboard = await self.get_repository_dashboard(owner, repo)
        if dashboard is None:
            return f"❌ فشل في جلب بيانات المستودع {owner}/{repo}"

        health_icon = {
            "healthy": "✅",
            "warning": "⚠️",
            "critical": "🚨",
        }.get(dashboard.health_status, "❓")

        text = (
            f"📦 **لوحة تحكم المستودع: {dashboard.full_name}**\n\n"
            f"{health_icon} **الحالة:** {dashboard.health_status}\n"
            f"🔒 **الخصوصية:** {'خاص' if dashboard.private else 'عام'}\n"
            f"🌿 **الفرع الافتراضي:** `{dashboard.default_branch}`\n"
            f"⭐ **النجوم:** {dashboard.stars} | 🍴 **الشفرات:** {dashboard.forks} | 👁 **المتابعون:** {dashboard.watchers}\n"
            f"🐛 **القضايا المفتوحة:** {dashboard.open_issues_count}\n"
            f"🔀 **طلبات السحب المفتوحة:** {dashboard.open_prs_count}\n"
            f"📤 **آخر دفع:** {dashboard.last_push or 'غير متاح'}\n"
            f"🕐 **آخر نشاط:** {dashboard.last_activity or 'غير متاح'}\n\n"
        )

        if dashboard.recent_activity:
            text += "📋 **الأنشطة الحديثة:**\n"
            for activity in dashboard.recent_activity[:5]:
                icon = "🔀" if activity["type"] == "pull_request" else "🐛"
                text += (
                    f"{icon} #{activity['number']} {activity['title'][:50]}...\n"
                    f"   المؤلف: {activity['author']} | {activity['created_at'][:10]}\n"
                )

        return text


class DashboardFormatter:
    """منسق لوحة التحكم للعرض في واجهات مختلفة."""

    @staticmethod
    def format_summary_for_telegram(summary: DashboardSummary) -> str:
        """تنسيق الملخص العام لعرض في Telegram."""
        return (
            "📊 **لوحة تحكم المشاريع - GitHub**\n\n"
            f"📦 **المستودعات:** {summary.total_repositories} إجمالي، {summary.active_repositories} نشط\n"
            f"🔀 **طلبات السحب:** {summary.total_pull_requests} إجمالي، {summary.open_pull_requests} مفتوح\n"
            f"🐛 **القضايا:** {summary.total_issues} إجمالي، {summary.open_issues} مفتوح\n"
            f"🏷️ **الإصدارات:** {summary.total_releases} إجمالي، {summary.published_releases} منشور\n"
            f"🌿 **الفروع:** {summary.total_branches} إجمالي، {summary.protected_branches} محمي\n\n"
            f"🕐 آخر تحديث: {summary.last_updated.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

    @staticmethod
    def format_repo_dashboard_for_telegram(dashboard: RepositoryDashboard) -> str:
        """تنسيق لوحة تحكم مستودع لعرض في Telegram."""
        health_icon = {
            "healthy": "✅",
            "warning": "⚠️",
            "critical": "🚨",
        }.get(dashboard.health_status, "❓")

        text = (
            f"📦 **لوحة تحكم المستودع: {dashboard.full_name}**\n\n"
            f"{health_icon} **الحالة:** {dashboard.health_status}\n"
            f"🔒 **الخصوصية:** {'خاص' if dashboard.private else 'عام'}\n"
            f"🌿 **الفرع الافتراضي:** `{dashboard.default_branch}`\n"
            f"⭐ **النجوم:** {dashboard.stars} | 🍴 **الشفرات:** {dashboard.forks} | 👁 **المتابعون:** {dashboard.watchers}\n"
            f"🐛 **القضايا المفتوحة:** {dashboard.open_issues_count}\n"
            f"🔀 **طلبات السحب المفتوحة:** {dashboard.open_prs_count}\n"
            f"📤 **آخر دفع:** {dashboard.last_push or 'غير متاح'}\n"
            f"🕐 **آخر نشاط:** {dashboard.last_activity or 'غير متاح'}\n\n"
        )

        if dashboard.recent_activity:
            text += "📋 **الأنشطة الحديثة:**\n"
            for activity in dashboard.recent_activity[:5]:
                icon = "🔀" if activity["type"] == "pull_request" else "🐛"
                text += (
                    f"{icon} #{activity['number']} {activity['title'][:50]}...\n"
                    f"   المؤلف: {activity['author']} | {activity['created_at'][:10]}\n"
                )

        return text
