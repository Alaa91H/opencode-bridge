import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx

from bridge.domain.tasks.retry_policy import ProviderTaskError, classify_retry, retry_after_seconds


class ProviderRetryPolicyTests(unittest.TestCase):
    def test_quota_errors_retry_without_limit(self):
        for status, code in ((402, "billing"), (429, "limit"), (403, "insufficient_quota"), (400, "quota_exceeded")):
            with self.subTest(status=status):
                error = ProviderTaskError({"data": {"statusCode": status, "message": code}})
                self.assertTrue(classify_retry(error).unlimited)

    def test_auth_and_unknown_forbidden_errors_are_permanent(self):
        for status in (401, 403, 404):
            self.assertIsNone(classify_retry(ProviderTaskError({"data": {"statusCode": status, "message": "denied"}})))
        self.assertIsNone(classify_retry(ValueError("invalid configuration")))

    def test_http_headers_and_structured_errors_preserve_retry_after(self):
        response = httpx.Response(429, headers={"Retry-After": "86400"}, request=httpx.Request("POST", "http://agent"))
        error = httpx.HTTPStatusError("quota", request=response.request, response=response)
        self.assertEqual(classify_retry(error).delay_seconds, 86400)
        embedded = ProviderTaskError({"data": {"statusCode": 429, "responseHeaders": {"Retry-After": "3600"}}})
        self.assertEqual(classify_retry(embedded).delay_seconds, 3600)

    def test_retry_after_dates_and_invalid_values(self):
        reset = format_datetime(datetime.now(UTC) + timedelta(hours=1), usegmt=True)
        self.assertGreater(retry_after_seconds(reset), 3590)
        for invalid in (None, "bad", "nan", "inf"):
            self.assertEqual(retry_after_seconds(invalid), 60)

    def test_transient_errors_preserve_pending_identity(self):
        self.assertTrue(classify_retry(httpx.ReadTimeout("timeout")).pending)
        self.assertFalse(classify_retry(ProviderTaskError({"data": {"statusCode": 503}})).unlimited)

    def test_opencode_retry_state_with_quota_message_is_unlimited_and_pending(self):
        error = ProviderTaskError({"data": {"statusCode": 503, "message": "Rate limit reached"}}, pending=True)
        decision = classify_retry(error)
        self.assertTrue(decision.unlimited)
        self.assertTrue(decision.pending)
