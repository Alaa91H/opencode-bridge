from __future__ import annotations

import unittest

from github_ci import GitHubCIError, failed_steps_from_jobs, summarize_run


class GitHubCIParsingTests(unittest.TestCase):
    def test_successful_run(self) -> None:
        status = summarize_run(
            "Alaa91H/opencode-bridge",
            {
                "id": 42,
                "name": "CI",
                "head_sha": "abc123",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/Alaa91H/opencode-bridge/actions/runs/42",
            },
        )
        self.assertEqual(status.state, "success")
        self.assertEqual(status.run_id, 42)
        self.assertEqual(status.workflow, "CI")

    def test_pending_run(self) -> None:
        status = summarize_run(
            "Alaa91H/opencode-bridge",
            {"id": 7, "status": "in_progress", "conclusion": None},
        )
        self.assertEqual(status.state, "pending")

    def test_failed_steps_are_compact_and_deduplicated(self) -> None:
        failures = failed_steps_from_jobs(
            {
                "jobs": [
                    {
                        "name": "tests",
                        "steps": [
                            {"name": "Checkout", "conclusion": "success"},
                            {"name": "Unit tests", "conclusion": "failure"},
                            {"name": "Unit tests", "conclusion": "failure"},
                        ],
                    },
                    {"name": "lint", "conclusion": "failure"},
                ]
            }
        )
        self.assertEqual(failures, ("tests: Unit tests", "lint"))

    def test_invalid_repository_is_rejected(self) -> None:
        with self.assertRaises(GitHubCIError):
            summarize_run("https://example.com/not-a-repo", None)


if __name__ == "__main__":
    unittest.main()
