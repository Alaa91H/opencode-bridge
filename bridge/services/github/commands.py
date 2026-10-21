"""Telegram commands for GitHub project management.

Provides Arabic Telegram commands for managing GitHub repositories,
pull requests, issues, releases, branches, and secrets through
the OpenCode Bridge.
"""

from __future__ import annotations

import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bridge.services.github.api import (
    GitHubAPIClient,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubConfig,
)
from bridge.services.github.branches import GitHubBranchManager
from bridge.services.github.issues import GitHubIssueManager
from bridge.services.github.pull_requests import GitHubPullRequestManager
from bridge.services.github.releases import GitHubReleaseManager
from bridge.services.github.repositories import (
    GitHubRepoConfig,
    GitHubRepositoryManager,
    LocalRepositoryManager,
)
from bridge.services.github.secrets import GitHubSecretManager

log = logging.getLogger("opencode_bridge.telegram.commands.github")

MAX_MESSAGE_LENGTH = 4096


def _format_arabic(text: str) -> str:
    """Format text for Arabic Telegram display."""
    return text


class GitHubCommandHandler:
    """Handler for GitHub-related Telegram commands.

    Registers command handlers for GitHub project management
    and wires them to the Telegram application.
    """

    def __init__(
        self,
        github_token: str | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.github_token = github_token
        self.workspace_root = workspace_root
        self._client: GitHubAPIClient | None = None
        self._repo_manager: GitHubRepositoryManager | None = None
        self._local_manager: LocalRepositoryManager | None = None
        self._pr_manager: GitHubPullRequestManager | None = None
        self._issue_manager: GitHubIssueManager | None = None
        self._release_manager: GitHubReleaseManager | None = None
        self._branch_manager: GitHubBranchManager | None = None
        self._secret_manager: GitHubSecretManager | None = None

    async def _ensure_client(self) -> GitHubAPIClient:
        """Ensure GitHub API client is initialized."""
        if self._client is None:
            config = GitHubConfig(token=self.github_token or "")
            self._client = GitHubAPIClient(config)
            await self._client.start()
            self._repo_manager = GitHubRepositoryManager(self._client)
            self._local_manager = LocalRepositoryManager(self.workspace_root)
            self._pr_manager = GitHubPullRequestManager(self._client)
            self._issue_manager = GitHubIssueManager(self._client)
            self._release_manager = GitHubReleaseManager(self._client)
            self._branch_manager = GitHubBranchManager(self._client)
            self._secret_manager = GitHubSecretManager(self._client)
        return self._client

    async def close(self) -> None:
        """Close the GitHub API client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ========================================================================
    # Command Handlers
    # ========================================================================

    async def cmd_github_repos(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List accessible GitHub repositories: /ghrepos [filter]"""
        _ = await self._ensure_client()
        repo_manager = self._repo_manager
        if repo_manager is None:
            return

        try:
            repos = await repo_manager.list_repositories()
            if not repos:
                await update.message.reply_text(
                    "لا توجد مستودعات متاحة أو لم يتم العثور على أي مستودعات."
                )
                return

            lines = ["📦 **المستودعات المتاحة على GitHub:**\n"]
            for repo in repos[:20]:
                private_icon = "🔒" if repo.private else "🌍"
                lines.append(
                    f"{private_icon} **{repo.full_name}**\n"
                    f"   الفرع الافتراضي: `{repo.default_branch}`\n"
                    f"   القضايا المفتوحة: {repo.open_issues_count}\n"
                    f"   الطلبات المفتوحة: {repo.stargazers_count} ⭐ | {repo.forks_count} 🍴\n"
                )

            if len(repos) > 20:
                lines.append(f"\nو {len(repos) - 20} مستودع آخر...")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except GitHubAuthenticationError:
            await update.message.reply_text(
                "❌ فشل في المصادقة مع GitHub. تأكد من إعداد GITHUB_TOKEN بشكل صحيح."
            )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub API: {exc}")

    async def cmd_github_create(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Create a new GitHub repository: /ghcreate <name> [description] [--public]"""
        if not context.args:
            await update.message.reply_text(
                "الاستخدام: `/ghcreate <اسم_المستودع> [الوصف] [--public]`",
                parse_mode="Markdown",
            )
            return

        name = context.args[0]
        description = None
        private = True

        args = context.args[1:]
        for i, arg in enumerate(args):
            if arg == "--public":
                private = False
                args.pop(i)
                break
            elif arg.startswith("--desc="):
                description = arg.split("=", 1)[1]
                args.pop(i)
                break

        if description is None and len(args) > 0:
            description = " ".join(args)

        _ = await self._ensure_client()
        repo_manager = self._repo_manager
        if repo_manager is None:
            return

        try:
            config = GitHubRepoConfig(
                name=name,
                description=description,
                private=private,
                auto_init=True,
            )
            repo_info = await repo_manager.create_repository(config)
            await update.message.reply_text(
                f"✅ تم إنشاء المستودع بنجاح!\n"
                f"📦 **{repo_info.full_name}**\n"
                f"🔗 الرابط: {repo_info.html_url}\n"
                f"🔒 الخصوصية: {'خاص' if repo_info.private else 'عام'}\n"
                f"🌿 الفرع الافتراضي: `{repo_info.default_branch}`\n"
                f"📋 الاستنساخ: `git clone {repo_info.clone_url}`",
                parse_mode="Markdown",
            )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ فشل في إنشاء المستودع: {exc}")

    async def cmd_github_clone(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Clone a GitHub repository locally: /ghclone <owner/repo> [branch]"""
        if not context.args:
            await update.message.reply_text(
                "الاستخدام: `/ghclone <المالك/المستودع> [الفرع]`",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        branch = context.args[1] if len(context.args) > 1 else None

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        repo_manager = self._repo_manager
        local_manager = self._local_manager
        if repo_manager is None:
            return
        if local_manager is None:
            return

        try:
            # Get repository info to get clone URL
            repo_info = await repo_manager.get_repository(owner, repo)

            # Check if workspace already exists
            if local_manager.workspace_exists(owner, repo):
                await update.message.reply_text(
                    f"⚠️ مساحة العمل موجودة بالفعل محليًا: `{owner}/{repo}`\n"
                    f"المسار: `{local_manager.get_workspace_path(owner, repo)}`",
                    parse_mode="Markdown",
                )
                return

            # Clone the repository
            workspace_path = await local_manager.clone_repository(
                repo_info.clone_url,
                owner,
                repo,
                branch=branch,
            )

            await update.message.reply_text(
                f"✅ تم استنساخ المستودع بنجاح!\n"
                f"📦 **{owner}/{repo}**\n"
                f"📁 المسار: `{workspace_path}`\n"
                f"🌿 الفرع: `{branch or repo_info.default_branch}`\n"
                f"🔗 الرابط: {repo_info.html_url}",
                parse_mode="Markdown",
            )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")
        except Exception as exc:
            await update.message.reply_text(f"❌ فشل في الاستنساخ: {exc}")

    async def cmd_github_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show status of local GitHub workspaces: /ghstatus [owner/repo]"""
        local_manager = self._local_manager
        if local_manager is None:
            return

        workspaces = local_manager.list_workspaces()

        if not workspaces:
            await update.message.reply_text(
                "لا توجد مساحات عمل محلية للمستودعات.\n"
                "استخدم `/ghclone <المالك/المستودع>` لاستنساخ مستودع."
            )
            return

        if context.args:
            repo_spec = context.args[0]
            if "/" not in repo_spec:
                await update.message.reply_text(
                    "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
                )
                return
            owner, repo = repo_spec.split("/", 1)
            workspaces = [(o, r, p) for o, r, p in workspaces if o == owner and r == repo]
            if not workspaces:
                await update.message.reply_text(
                    f"❌ مساحة العمل غير موجودة: `{owner}/{repo}`"
                )
                return

        lines = ["📁 **مساحات العمل المحلية:**\n"]
        for owner, repo, path in workspaces:
            try:
                status = await local_manager.get_repository_status(owner, repo)
                changes_icon = "✅" if not status["has_changes"] else "⚠️"
                lines.append(
                    f"{changes_icon} **{owner}/{repo}**\n"
                    f"   المسار: `{path}`\n"
                    f"   الملفات المعدلة: {status['change_count']}\n"
                )
            except Exception as exc:
                lines.append(
                    f"❌ **{owner}/{repo}**\n"
                    f"   خطأ: {exc}\n"
                )

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_github_pr(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List or create Pull Requests: /ghpr <owner/repo> [action] [args]"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "الاستخدام:\n"
                "• `/ghpr <المالك/المستودع> list` - عرض طلبات السحب\n"
                "• `/ghpr <المالك/المستودع> create <العنوان> <الفرع_مصدر> <الفرع_هدف> [الوصف]` - إنشاء طلب سحب",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        action = context.args[1].lower()

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        pr_manager = self._pr_manager
        if pr_manager is None:
            return

        try:
            if action == "list":
                prs = await pr_manager.list_pull_requests(owner, repo)
                if not prs:
                    await update.message.reply_text(
                        f"لا توجد طلبات سحب مفتوحة في `{owner}/{repo}`.",
                        parse_mode="Markdown",
                    )
                    return

                lines = [f"🔀 **طلبات السحب في {owner}/{repo}:**\n"]
                for pr in prs[:15]:
                    draft_icon = "📝" if pr.draft else ""
                    lines.append(
                        f"{draft_icon} **#{pr.number}** {pr.title}\n"
                        f"   المؤلف: `{pr.author}` | `{pr.head_ref}` → `{pr.base_ref}`\n"
                        f"   الحالة: `{pr.state}` | التعليقات: {pr.comments}\n"
                    )
                if len(prs) > 15:
                    lines.append(f"\nو {len(prs) - 15} طلب سحب آخر...")
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

            elif action == "create":
                if len(context.args) < 5:
                    await update.message.reply_text(
                        "الاستخدام: `/ghpr <المالك/المستودع> create <العنوان> <الفرع_مصدر> <الفرع_هدف> [الوصف]`",
                        parse_mode="Markdown",
                    )
                    return

                title = context.args[2]
                head = context.args[3]
                base = context.args[4]
                body = context.args[5] if len(context.args) > 5 else None

                pr = await pr_manager.create_pull_request(
                    owner, repo, title, head, base, body=body
                )
                await update.message.reply_text(
                    f"✅ تم إنشاء طلب السحب بنجاح!\n"
                    f"🔀 **#{pr.number}** {pr.title}\n"
                    f"🔗 الرابط: {pr.html_url}\n"
                    f"🌿 `{pr.head_ref}` → `{pr.base_ref}`\n"
                    f"👤 المؤلف: `{pr.author}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ إجراء غير معروف. استخدم `list` أو `create`."
                )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")

    async def cmd_github_issue(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List or create Issues: /ghissue <owner/repo> [action] [args]"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "الاستخدام:\n"
                "• `/ghissue <المالك/المستودع> list` - عرض القضايا\n"
                "• `/ghissue <المالك/المستودع> create <العنوان> [الوصف]` - إنشاء قضية",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        action = context.args[1].lower()

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        issue_manager = self._issue_manager
        if issue_manager is None:
            return

        try:
            if action == "list":
                issues = await issue_manager.list_issues(owner, repo)
                if not issues:
                    await update.message.reply_text(
                        f"لا توجد قضايا مفتوحة في `{owner}/{repo}`.",
                        parse_mode="Markdown",
                    )
                    return

                lines = [f"🐛 **القضايا في {owner}/{repo}:**\n"]
                for issue in issues[:15]:
                    lines.append(
                        f"#{issue.number} **{issue.title}**\n"
                        f"   المؤلف: `{issue.author}` | الحالة: `{issue.state}`\n"
                        f"   التعليقات: {issue.comments}\n"
                    )
                if len(issues) > 15:
                    lines.append(f"\nو {len(issues) - 15} قضية أخرى...")
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

            elif action == "create":
                if len(context.args) < 3:
                    await update.message.reply_text(
                        "الاستخدام: `/ghissue <المالك/المستودع> create <العنوان> [الوصف]`",
                        parse_mode="Markdown",
                    )
                    return

                title = context.args[2]
                body = context.args[3] if len(context.args) > 3 else None

                issue = await issue_manager.create_issue(
                    owner, repo, title, body=body
                )
                await update.message.reply_text(
                    f"✅ تم إنشاء المسألة بنجاح!\n"
                    f"🐛 **#{issue.number}** {issue.title}\n"
                    f"🔗 الرابط: {issue.html_url}\n"
                    f"👤 المؤلف: `{issue.author}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ إجراء غير معروف. استخدم `list` أو `create`."
                )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")

    async def cmd_github_release(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List or create Releases: /ghrelease <owner/repo> [action] [args]"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "الاستخدام:\n"
                "• `/ghrelease <المالك/المستودع> list` - عرض الإصدارات\n"
                "• `/ghrelease <المالك/المستودع> create <العلامة> <الاسم> [الملاحظات]` - إنشاء إصدار",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        action = context.args[1].lower()

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        release_manager = self._release_manager
        if release_manager is None:
            return

        try:
            if action == "list":
                releases = await release_manager.list_releases(owner, repo)
                if not releases:
                    await update.message.reply_text(
                        f"لا توجد إصدارات في `{owner}/{repo}`.",
                        parse_mode="Markdown",
                    )
                    return

                lines = [f"🏷️ **الإصدارات في {owner}/{repo}:**\n"]
                for rel in releases[:15]:
                    draft_icon = "📝" if rel.draft else ""
                    pre_icon = "⚠️" if rel.prerelease else ""
                    lines.append(
                        f"{draft_icon}{pre_icon} **{rel.tag_name}**\n"
                        f"   الاسم: {rel.name or 'غير محدد'}\n"
                        f"   الحالة: `{rel.state}` | الأصول: {len(rel.assets)}\n"
                    )
                if len(releases) > 15:
                    lines.append(f"\nو {len(releases) - 15} إصدار آخر...")
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

            elif action == "create":
                if len(context.args) < 4:
                    await update.message.reply_text(
                        "الاستخدام: `/ghrelease <المالك/المستودع> create <العلامة> <الاسم> [الملاحظات]`",
                        parse_mode="Markdown",
                    )
                    return

                tag_name = context.args[2]
                name = context.args[3]
                body = context.args[4] if len(context.args) > 4 else None

                release = await release_manager.create_release(
                    owner, repo, tag_name, name=name, body=body
                )
                await update.message.reply_text(
                    f"✅ تم إنشاء الإصدار بنجاح!\n"
                    f"🏷️ **{release.tag_name}**\n"
                    f"📛 الاسم: {release.name or 'غير محدد'}\n"
                    f"🔗 الرابط: {release.html_url}\n"
                    f"📋 الحالة: `{'مسودة' if release.draft else 'مُنشر'}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ إجراء غير معروف. استخدم `list` أو `create`."
                )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")

    async def cmd_github_branch(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List or create branches: /ghbranch <owner/repo> [action] [args]"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "الاستخدام:\n"
                "• `/ghbranch <المالك/المستودع> list` - عرض الفروع\n"
                "• `/ghbranch <المالك/المستودع> create <اسم_الفرع> [من_الفرع]` - إنشاء فرع",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        action = context.args[1].lower()

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        branch_manager = self._branch_manager
        if branch_manager is None:
            return

        try:
            if action == "list":
                branches = await branch_manager.list_branches(owner, repo)
                if not branches:
                    await update.message.reply_text(
                        f"لا توجد فروع في `{owner}/{repo}`.",
                        parse_mode="Markdown",
                    )
                    return

                lines = [f"🌿 **الفروع في {owner}/{repo}:**\n"]
                for branch in branches[:20]:
                    protected_icon = "🔒" if branch.protected else ""
                    lines.append(
                        f"{protected_icon} **{branch.name}**\n"
                        f"  commit: `{branch.commit_sha[:8]}`\n"
                    )
                if len(branches) > 20:
                    lines.append(f"\nو {len(branches) - 20} فرع آخر...")
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

            elif action == "create":
                if len(context.args) < 3:
                    await update.message.reply_text(
                        "الاستخدام: `/ghbranch <المالك/المستودع> create <اسم_الفرع> [من_الفرع]`",
                        parse_mode="Markdown",
                    )
                    return

                branch_name = context.args[2]
                from_branch = context.args[3] if len(context.args) > 3 else None

                new_branch = await branch_manager.create_branch(
                    owner, repo, branch_name, from_branch=from_branch
                )
                await update.message.reply_text(
                    f"✅ تم إنشاء الفرع بنجاح!\n"
                    f"🌿 **{new_branch.name}**\n"
                    f"🔗 commit: `{new_branch.commit_sha[:8]}`\n"
                    f"📦 المستودع: `{owner}/{repo}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ إجراء غير معروف. استخدم `list` أو `create`."
                )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")

    async def cmd_github_secret(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Manage repository secrets: /ghsecret <owner/repo> [action] [args]"""
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "الاستخدام:\n"
                "• `/ghsecret <المالك/المستودع> list` - عرض الأسرار\n"
                "• `/ghsecret <المالك/المستودع> set <الاسم> <القيمة>` - تعيين سر\n"
                "• `/ghsecret <المالك/المستودع> delete <الاسم>` - حذف سر",
                parse_mode="Markdown",
            )
            return

        repo_spec = context.args[0]
        action = context.args[1].lower()

        if "/" not in repo_spec:
            await update.message.reply_text(
                "❌ يرجى تحديد المستودع بالصيغة: `<المالك/المستودع>`"
            )
            return

        owner, repo = repo_spec.split("/", 1)

        _ = await self._ensure_client()
        secret_manager = self._secret_manager
        if secret_manager is None:
            return

        try:
            if action == "list":
                secrets = await secret_manager.list_secrets(owner, repo)
                if not secrets:
                    await update.message.reply_text(
                        f"لا توجد أسرار مخزنة في `{owner}/{repo}`.",
                        parse_mode="Markdown",
                    )
                    return

                lines = [f"🔐 **الأسرار في {owner}/{repo}:**\n"]
                for secret in secrets:
                    lines.append(
                        f"• `{secret.name}`\n"
                        f"   الإنشاء: {secret.created_at[:10]} | التحديث: {secret.updated_at[:10]}\n"
                    )
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

            elif action == "set":
                if len(context.args) < 4:
                    await update.message.reply_text(
                        "الاستخدام: `/ghsecret <المالك/المستودع> set <الاسم> <القيمة>`",
                        parse_mode="Markdown",
                    )
                    return

                secret_name = context.args[2]
                secret_value = context.args[3]

                await secret_manager.create_or_update_secret(
                    owner, repo, secret_name, secret_value
                )
                await update.message.reply_text(
                    f"✅ تم حفظ السر بنجاح!\n"
                    f"🔐 الاسم: `{secret_name}`\n"
                    f"📦 المستودع: `{owner}/{repo}`\n"
                    f"⚠️ القيمة مُشفرة ولم تُخزن كنص عادي.",
                    parse_mode="Markdown",
                )

            elif action == "delete":
                if len(context.args) < 3:
                    await update.message.reply_text(
                        "الاستخدام: `/ghsecret <المالك/المستودع> delete <الاسم>`",
                        parse_mode="Markdown",
                    )
                    return

                secret_name = context.args[2]
                await secret_manager.delete_secret(owner, repo, secret_name)
                await update.message.reply_text(
                    f"✅ تم حذف السر بنجاح!\n"
                    f"🔐 الاسم: `{secret_name}`\n"
                    f"📦 المستودع: `{owner}/{repo}`",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "❌ إجراء غير معروف. استخدم `list` أو `set` أو `delete`."
                )
        except GitHubAPIError as exc:
            await update.message.reply_text(f"❌ خطأ في GitHub: {exc}")

    async def cmd_github_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show GitHub commands help: /ghhelp"""
        help_text = """
🔗 **أوامر إدارة مشاريع GitHub**

**المستودعات:**
• `/ghrepos` - عرض جميع المستودعات المتاحة
• `/ghcreate <اسم> [الوصف] [--public]` - إنشاء مستودع جديد
• `/ghclone <المالك/المستودع> [الفرع]` - استنساخ مستودع محليًا
• `/ghstatus [المالك/المستودع]` - حالة مساحات العمل المحلية

**طلبات السحب (Pull Requests):**
• `/ghpr <المالك/المستودع> list` - عرض طلبات السحب
• `/ghpr <المالك/المستودع> create <العنوان> <المصدر> <الهدف> [الوصف]` - إنشاء طلب سحب

**القضايا (Issues):**
• `/ghissue <المالك/المستودع> list` - عرض القضايا
• `/ghissue <المالك/المستودع> create <العنوان> [الوصف]` - إنشاء قضية

**الإصدارات (Releases):**
• `/ghrelease <المالك/المستودع> list` - عرض الإصدارات
• `/ghrelease <المالك/المستودع> create <العلامة> <الاسم> [الملاحظات]` - إنشاء إصدار

**الفروع (Branches):**
• `/ghbranch <المالك/المستودع> list` - عرض الفروع
• `/ghbranch <المالك/المستودع> create <اسم_الفرع> [من_الفرع]` - إنشاء فرع

**الأسرار (Secrets):**
• `/ghsecret <المالك/المستودع> list` - عرض الأسرار
• `/ghsecret <المالك/المستودع> set <الاسم> <القيمة>` - تعيين سر
• `/ghsecret <المالك/المستودع> delete <الاسم>` - حذف سر

**الإعداد المطلوب:**
أضف `GITHUB_TOKEN` إلى ملف `.env` مع توكن GitHub شخصي أو توكن تطبيق.

لمزيد من المساعدة، راجع وثائق المشروع.
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")

    def register_handlers(self, application) -> None:
        """Register all GitHub command handlers with the Telegram application."""
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghrepos", self.cmd_github_repos)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghcreate", self.cmd_github_create)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghclone", self.cmd_github_clone)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghstatus", self.cmd_github_status)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghpr", self.cmd_github_pr)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghissue", self.cmd_github_issue)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghrelease", self.cmd_github_release)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghbranch", self.cmd_github_branch)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghsecret", self.cmd_github_secret)
        )
        application.add_handler(
            __import__("telegram.ext").CommandHandler("ghhelp", self.cmd_github_help)
        )
