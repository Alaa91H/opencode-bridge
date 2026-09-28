from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V2BaselineContractTests(unittest.TestCase):
    """Lock the pre-v2 production behavior before architectural refactoring."""

    def test_production_entrypoint_is_v3_wrapper(self) -> None:
        service = (ROOT / "deploy" / "opencode-bridge-telegram.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/home/ubuntu/opencode-bridge/venv/bin/python -m run_v3", service)
        run_v3 = (ROOT / "run_v3.py").read_text(encoding="utf-8")
        self.assertIn("import bot as core", run_v3)
        self.assertIn("await v3_plugin.install(app)", run_v3)
        self.assertIn("await ci_plugin.install(app)", run_v3)
        self.assertIn("await resource_commands.install(app)", run_v3)
        self.assertIn("await watchdog_plugin.install(app)", run_v3)

    def test_current_sqlite_schema_sources_are_accounted_for(self) -> None:
        expected = {
            "agent_tasks",
            "scheduled_jobs",
            "pending_attachment_batches",
            "sessions",
            "free_points_usage",
            "active_workspaces",
        }
        found: set[str] = set()
        pattern = re.compile(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.IGNORECASE,
        )
        schema_sources = [*ROOT.glob("*.py"), ROOT / "bridge" / "infrastructure" / "database" / "migrations.py"]
        for path in schema_sources:
            found.update(pattern.findall(path.read_text(encoding="utf-8")))
        # T04 adds new v2 tables in the migration registry; this baseline assertion
        # continues to require that every original v1.8 table remains represented.
        found.intersection_update(expected)
        self.assertEqual(found, expected)

    def test_attachment_limits_match_v18_baseline(self) -> None:
        source = (ROOT / "attachments.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024", source)
        self.assertIn("DEFAULT_MAX_ATTACHMENTS_PER_TASK = 10", source)
        self.assertIn("DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024", source)
        self.assertIn("DEFAULT_MAX_OUTGOING_FILES = 10", source)

        env = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_ATTACHMENT_MAX_BYTES=20971520", env)
        self.assertIn("TELEGRAM_ATTACHMENT_MAX_COUNT=10", env)
        self.assertIn("TELEGRAM_ATTACHMENT_MAX_TOTAL_BYTES=52428800", env)
        self.assertIn("TELEGRAM_ATTACHMENT_PENDING_SECONDS=600", env)
        self.assertIn("TELEGRAM_MEDIA_GROUP_DEBOUNCE_SECONDS=1.25", env)

    def test_opencode_configurations_are_currently_identical(self) -> None:
        primary = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        v3 = json.loads((ROOT / "opencode-v3.json").read_text(encoding="utf-8"))
        self.assertEqual(primary, v3)
        self.assertEqual(primary["server"]["hostname"], "127.0.0.1")
        self.assertEqual(primary["server"]["port"], 4096)
        self.assertEqual(primary["share"], "manual")
        self.assertTrue(primary["snapshot"])
        self.assertEqual(primary["permission"]["read"]["*.env"], "deny")
        self.assertEqual(primary["permission"]["edit"]["*.key"], "deny")
        self.assertEqual(primary["permission"]["bash"]["git push --force*"], "deny")
        self.assertEqual(primary["permission"]["bash"]["pip install*"], "deny")
        self.assertEqual(primary["permission"]["bash"]["reboot*"], "deny")

    def test_v3_workspace_tasks_use_the_single_message_lifecycle(self) -> None:
        plugin = (ROOT / "v3_plugin.py").read_text(encoding="utf-8")
        adapter = (ROOT / "bridge" / "telegram" / "commands" / "workspaces.py").read_text(encoding="utf-8")
        service = (ROOT / "bridge" / "services" / "workspace_service.py").read_text(encoding="utf-8")
        task_service = (ROOT / "bridge" / "services" / "task_service.py").read_text(encoding="utf-8")
        combined = "\n".join((plugin, adapter, service, task_service))
        self.assertNotIn("Queued task #", combined)
        self.assertNotIn("Queued development task #", combined)
        self.assertIn("status_message_id = await self.create_status", adapter)
        self.assertIn("status_message_id=status_message_id", adapter)
        self.assertIn("status_message_id=status_message_id", service)
        self.assertIn("stored_prompt=prepared", service)

    def test_base_task_ui_does_not_restore_chunked_numbered_results(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        delivery = (ROOT / "bridge" / "telegram" / "execution.py").read_text(encoding="utf-8")
        execution = (ROOT / "bridge" / "services" / "task_execution_service.py").read_text(encoding="utf-8")
        combined = "\n".join((source, delivery, execution))
        self.assertIn("_create_task_status_message", source)
        self.assertIn("def final_text", delivery)
        self.assertNotIn("def _task_reply_chunks", combined)
        self.assertNotIn("ملف ناتج عن المهمة #", combined)

    def test_named_persistent_schedule_management_is_present(self) -> None:
        source = (ROOT / "task_queue.py").read_text(encoding="utf-8")
        for method in (
            "create_scheduled_job",
            "get_scheduled_job",
            "list_scheduled_jobs",
            "rename_scheduled_job",
            "set_scheduled_job_prompt",
            "append_scheduled_job_prompt",
            "update_scheduled_job_timing",
            "set_scheduled_job_enabled",
            "delete_scheduled_job",
            "enqueue_scheduled_job_now",
        ):
            self.assertIn(f"async def {method}", source)

    def test_deployment_unit_inventory_is_stable(self) -> None:
        units = {
            path.name
            for directory in (ROOT / "deploy", ROOT / "maintenance")
            for path in directory.iterdir()
            if path.suffix in {".service", ".timer", ".target"}
        }
        self.assertEqual(
            units,
            {
                "opencode-bridge-telegram.service",
                "opencode-bridge.target",
                "opencode-serve.service",
                "opencode-bridge-maintenance.service",
                "opencode-bridge-maintenance.timer",
                "opencode-bridge-reboot-guard.service",
                "opencode-bridge-resource-optimizer.service",
            },
        )


if __name__ == "__main__":
    unittest.main()
