import unittest
from unittest.mock import patch

from bridge.infrastructure.tracing.pipeline import TRACE_STAGES, child_stage
from bridge.infrastructure.tracing.tracing import (
    SamplingPolicy, TraceContext, activate, current, redact_attributes, reset,
)


class TracingTests(unittest.TestCase):
    def test_trace_id_propagates_across_required_pipeline(self):
        root = TraceContext.root(SamplingPolicy(1))
        spans = [child_stage(root, stage) for stage in TRACE_STAGES]
        self.assertTrue(all(s.context.trace_id == root.trace_id for s in spans))
        self.assertEqual(len({s.context.span_id for s in spans}), len(spans))
        self.assertTrue(all(s.headers["traceparent"].startswith("00-" + root.trace_id + "-") for s in spans))

    def test_contextvar_propagation(self):
        root = TraceContext.root()
        token = activate(root)
        try:
            self.assertEqual(current(), root)
        finally:
            reset(token)
        self.assertIsNone(current())

    def test_sampling_policy(self):
        with patch("bridge.infrastructure.tracing.tracing.random.random", return_value=.8):
            self.assertFalse(TraceContext.root(SamplingPolicy(.5)).sampled)
        with self.assertRaises(ValueError):
            SamplingPolicy(1.1)

    def test_privacy_redaction(self):
        clean = redact_attributes({
            "credential_token": "hidden",
            "prompt": "private text",
            "file_content": "bytes",
            "owner_id": "owner",
            "component": "opencode",
        })
        self.assertEqual(clean["credential_token"], "[REDACTED]")
        self.assertEqual(clean["prompt"], "[CONTENT_REDACTED]")
        self.assertNotEqual(clean["owner_id"], "owner")
        self.assertEqual(clean["component"], "opencode")
