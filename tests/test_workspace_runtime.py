from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from workspace_manager import GitWorkspaceManager, WorkspaceError
from workspace_runtime import WorkspaceOpenCodeClient, workspace_from_prompt


class WorkspaceOpenCodeClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            await client.close()

    async def test_directory_scope_injects_header_without_leaking(self) -> None:
        self.client = WorkspaceOpenCodeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = str(Path(temp_dir).resolve())
            request = httpx.Request("GET", "http://127.0.0.1:4096/path")
            with self.client.directory_scope(temp_dir):
                await self.client._inject_directory(request)
            self.assertEqual(request.headers["x-opencode-directory"], expected)

            next_request = httpx.Request("GET", "http://127.0.0.1:4096/path")
            await self.client._inject_directory(next_request)
            self.assertNotIn("x-opencode-directory", next_request.headers)

    async def test_session_registry_restores_directory_outside_scope(self) -> None:
        self.client = WorkspaceOpenCodeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            expected = str(Path(temp_dir).resolve())
            self.client._session_directories["ses_test"] = expected
            request = httpx.Request("POST", "http://127.0.0.1:4096/session/ses_test/message")
            await self.client._inject_directory(request)
            self.assertEqual(request.headers["x-opencode-directory"], expected)


class WorkspacePromptTests(unittest.TestCase):
    def test_valid_workspace_context_resolves_allowed_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            manager = GitWorkspaceManager(root, ["Alaa91H/opencode-bridge"])
            directory = manager.repo_path("Alaa91H/opencode-bridge")
            prompt = (
                "ACTIVE_WORKSPACE (trusted bridge context)\n"
                "repository: Alaa91H/opencode-bridge\n"
                f"directory: {directory}\n"
                "policy: work only inside this repository; do not build or install dependencies locally.\n"
                "END_ACTIVE_WORKSPACE\n\n"
                "USER_REQUEST:\nImprove reliability"
            )
            slug, resolved = workspace_from_prompt(prompt, manager) or (None, None)
            self.assertEqual(slug, "Alaa91H/opencode-bridge")
            self.assertEqual(resolved, directory)

    def test_spoofed_workspace_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = GitWorkspaceManager(Path(temp_dir), ["Alaa91H/opencode-bridge"])
            prompt = (
                "ACTIVE_WORKSPACE (trusted bridge context)\n"
                "repository: Alaa91H/opencode-bridge\n"
                "directory: /tmp/not-the-allowed-workspace\n"
                "policy: work only inside this repository; do not build or install dependencies locally.\n"
                "END_ACTIVE_WORKSPACE\n\n"
                "USER_REQUEST:\nDo work"
            )
            with self.assertRaises(WorkspaceError):
                workspace_from_prompt(prompt, manager)


if __name__ == "__main__":
    unittest.main()
