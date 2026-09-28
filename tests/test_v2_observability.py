import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.metrics.observability import (
    REQUIRED_METRICS, MetricsRegistry, PrometheusEndpoint,
)
from bridge.infrastructure.metrics.resources import sample_resources


class ObservabilityTests(unittest.TestCase):
    def test_all_required_metrics_exist(self):
        registry = MetricsRegistry()
        self.assertEqual(set(registry.snapshot()), set(REQUIRED_METRICS))

    def test_counters_gauges_latency_and_failures(self):
        registry = MetricsRegistry()
        registry.set("queue_depth", 12)
        registry.observe("task_latency_seconds", .2)
        registry.observe("task_duration_seconds", 3.4)
        registry.inc("retry_count")
        registry.inc("model_failures", 2)
        registry.inc("telegram_failures")
        registry.observe("db_latency_seconds", .01)
        registry.inc("db_locks")
        registry.inc("attachment_bytes_total", 1024)
        registry.set("active_workers", 4)
        registry.observe("schedule_lag_seconds", 2.5)
        values = registry.snapshot()
        self.assertEqual(values["queue_depth"], 12)
        self.assertEqual(values["model_failures"], 2)
        self.assertEqual(values["attachment_bytes_total"], 1024)

    def test_prometheus_endpoint_is_optional(self):
        registry = MetricsRegistry()
        disabled = PrometheusEndpoint(registry)
        self.assertEqual(disabled.render()[0], 404)
        status, content_type, body = PrometheusEndpoint(registry, enabled=True).render()
        self.assertEqual(status, 200)
        self.assertIn("version=0.0.4", content_type)
        for metric in REQUIRED_METRICS:
            self.assertIn(f"opencode_bridge_{metric} ", body)

    def test_resource_sampling(self):
        registry = MetricsRegistry()
        with tempfile.TemporaryDirectory() as tmp:
            sample_resources(registry, disk_path=Path(tmp))
        values = registry.snapshot()
        self.assertGreater(values["disk_free_bytes"], 0)
        self.assertGreaterEqual(values["cpu_percent"], 0)

    def test_unknown_and_negative_counter_rejected(self):
        registry = MetricsRegistry()
        with self.assertRaises(KeyError):
            registry.inc("unknown")
        with self.assertRaises(ValueError):
            registry.set("queue_depth", -1)
