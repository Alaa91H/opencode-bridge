import asyncio
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.database.migrations import MigrationRunner
from bridge.infrastructure.database.sqlite import BridgeDatabase


class QueryPlanTests(unittest.TestCase):
    def test_critical_queries_use_expected_indexes(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                database = BridgeDatabase(Path(tmp) / "bridge.db")
                await MigrationRunner(database).migrate()
                db = await database.connect()
                cases = (
                    (
                        "SELECT * FROM agent_tasks WHERE owner_id=? AND status=? ORDER BY sequence,id LIMIT 1",
                        ("owner", "queued"),
                        "idx_agent_tasks_owner_status_sequence",
                    ),
                    (
                        "SELECT * FROM agent_tasks WHERE status=? AND due_at<=?",
                        ("scheduled", "9999"),
                        "idx_agent_tasks_status_due",
                    ),
                    (
                        "SELECT * FROM scheduled_jobs WHERE enabled=1 AND next_run_at<=?",
                        ("9999",),
                        "idx_scheduled_jobs_due",
                    ),
                    (
                        "SELECT * FROM pending_attachment_batches WHERE expires_at<=?",
                        ("9999",),
                        "idx_pending_attachment_expiry",
                    ),
                )
                for query, params, expected in cases:
                    async with db.execute("EXPLAIN QUERY PLAN " + query, params) as cursor:
                        details = " ".join(str(row[3]) for row in await cursor.fetchall())
                    self.assertIn(expected, details, details)
                await database.close()
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
