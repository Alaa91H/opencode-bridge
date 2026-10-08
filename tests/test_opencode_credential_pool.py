from datetime import UTC, datetime
import unittest

from bridge.infrastructure.opencode.credential_pool import CredentialPool, OpenCodeCredential, parse_credential_pool


class CredentialPoolTests(unittest.TestCase):
    def test_accepts_unbounded_dynamic_entries_and_rotates(self):
        credentials = parse_credential_pool(";".join(f"account-{i}=secret-{i}" for i in range(25)))
        pool = CredentialPool(credentials)
        self.assertEqual(len(pool), 25)
        self.assertEqual([pool.select().name for _ in range(3)], ["account-0", "account-1", "account-2"])

    def test_credential_quota_cools_only_that_credential(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
        pool = CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("b", "two")))
        first = pool.select(now=now)
        self.assertTrue(pool.record_failure(first.name, "credential_quota", retry_after=120, now=now))
        self.assertEqual(pool.select(now=now).name, "b")
        states = {item.name: item.state for item in pool.snapshots(now=now)}
        self.assertEqual(states, {"a": "cooldown", "b": "ready"})

    def test_zen_free_ip_quota_never_rotates_credentials(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
        pool = CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("b", "two")))
        first = pool.select(now=now)
        self.assertFalse(pool.record_failure(first.name, "zen_free_quota", now=now))
        self.assertTrue(all(item.state == "ready" for item in pool.snapshots(now=now)))

    def test_auth_failure_disables_only_failed_credential(self):
        pool = CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("b", "two")))
        self.assertTrue(pool.record_failure("a", "auth"))
        self.assertEqual(pool.select().name, "b")
        self.assertEqual(pool.snapshots()[0].state, "disabled")

    def test_snapshots_never_expose_secrets(self):
        pool = CredentialPool((OpenCodeCredential("primary", "super-secret-value"),))
        snapshot = pool.snapshots()[0]
        self.assertEqual(snapshot.name, "primary")
        self.assertNotEqual(snapshot.fingerprint, "super-secret-value")
        self.assertNotIn("super-secret-value", repr(snapshot))

    def test_duplicate_names_and_invalid_entries_are_rejected(self):
        with self.assertRaises(ValueError):
            CredentialPool((OpenCodeCredential("a", "one"), OpenCodeCredential("a", "two")))
        with self.assertRaises(ValueError):
            parse_credential_pool("missing-separator")


if __name__ == "__main__":
    unittest.main()
