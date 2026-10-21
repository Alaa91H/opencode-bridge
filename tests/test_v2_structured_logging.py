import json
import logging
import tempfile
import unittest
from pathlib import Path

from bridge.infrastructure.logging.structured import JsonFormatter, build_logger, safe_extra


class StructuredLoggingTests(unittest.TestCase):
    def test_json_context_and_owner_hash(self):
        record = logging.LogRecord("x", logging.INFO, "", 0, "done", (), None)
        record.event = "task.completed"
        record.task = "t1"
        record.schedule = "s1"
        record.owner = "private-owner"
        record.trace = "trace"
        record.attempt = 2
        record.component = "worker"
        record.duration = .4
        record.status = "ok"
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["event"], "task.completed")
        self.assertEqual(payload["task"], "t1")
        self.assertNotEqual(payload["owner_hash"], "private-owner")
        self.assertEqual(payload["status"], "ok")

    def test_prompt_and_file_content_not_logged_by_safe_extra(self):
        clean = safe_extra(prompt="full prompt", file_content="private", component="media")
        self.assertNotIn("prompt", clean)
        self.assertNotIn("file_content", clean)
        self.assertEqual(clean["component"], "media")

    def test_secret_attributes_are_redacted(self):
        record = logging.LogRecord("x", logging.INFO, "", 0, "request", (), None)
        record.attributes = {"credential_token": "hidden", "component": "api"}
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["attributes"]["credential_token"], "[REDACTED]")

    def test_rotation_policy_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = build_logger("rotation-test", Path(tmp) / "bridge.log", max_bytes=100, backups=2)
            for _ in range(20):
                logger.info("x" * 80)
            for handler in logger.handlers:
                handler.flush()
            files = list(Path(tmp).glob("bridge.log*"))
            self.assertLessEqual(len(files), 3)

    def test_invalid_retention_policy_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                build_logger("bad", Path(tmp) / "x.log", backups=-1)
