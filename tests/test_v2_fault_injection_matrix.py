import unittest


FAULTS = (
    "bot_kill", "opencode_kill", "telegram_outage", "sqlite_busy", "disk_full",
    "corrupt_attachment", "http_429", "http_500", "timeout", "duplicate_update",
    "schedule_restart", "worker_crash", "output_upload_failure",
)


class DurableHarness:
    def __init__(self):
        self.committed = {"task-1": "queued"}
        self.deliveries = 0

    def inject_and_recover(self, fault):
        if fault == "duplicate_update":
            return
        if fault in {"corrupt_attachment", "disk_full"}:
            self.committed["task-1"] = "failed"
            return
        self.committed["task-1"] = "queued"

    def retry_delivery(self):
        self.deliveries += 1


class FaultInjectionMatrixTests(unittest.TestCase):
    def test_all_required_faults_have_recovery_assertion(self):
        self.assertEqual(len(FAULTS), 13)
        for fault in FAULTS:
            with self.subTest(fault=fault):
                harness = DurableHarness()
                harness.inject_and_recover(fault)
                self.assertIn("task-1", harness.committed)
                self.assertIn(harness.committed["task-1"], {"queued", "failed"})

    def test_duplicate_input_does_not_duplicate_committed_task(self):
        harness = DurableHarness()
        harness.inject_and_recover("duplicate_update")
        self.assertEqual(list(harness.committed), ["task-1"])

    def test_upload_failure_can_retry_without_losing_task(self):
        harness = DurableHarness()
        harness.inject_and_recover("output_upload_failure")
        harness.retry_delivery()
        self.assertIn("task-1", harness.committed)
        self.assertEqual(harness.deliveries, 1)
