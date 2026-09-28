import unittest

from bridge.infrastructure.metrics.observability import REQUIRED_METRICS


class PerformanceTargetContractTests(unittest.TestCase):
    def test_critical_performance_metrics_exist(self):
        required = {
            "queue_depth", "task_latency_seconds", "task_duration_seconds",
            "db_latency_seconds", "db_locks", "memory_bytes", "cpu_percent",
            "disk_free_bytes", "active_workers", "schedule_lag_seconds",
        }
        self.assertTrue(required.issubset(REQUIRED_METRICS))

    def test_targets_are_backed_by_required_test_suites(self):
        suites = {
            "durable_queue": "tests/test_v2_durable_queue.py",
            "streaming": "tests/test_v2_streaming.py",
            "fault": "tests/test_v2_fault_injection_matrix.py",
            "load": "tests/test_v2_load_soak.py",
        }
        from pathlib import Path
        for path in suites.values():
            self.assertTrue(Path(path).is_file(), path)
