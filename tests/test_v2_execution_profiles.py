import unittest

from bridge.domain.policies.execution_profiles import CAPABILITIES, ExecutionPolicy, ExecutionProfile


class ExecutionProfileTests(unittest.TestCase):
    def test_safe_is_default_and_minimum(self):
        policy = ExecutionPolicy()
        self.assertEqual(policy.select(owner="u").profile, ExecutionProfile.SAFE)
        self.assertNotIn("host_admin", CAPABILITIES[ExecutionProfile.SAFE])

    def test_task_override_precedes_user_profile(self):
        policy = ExecutionPolicy()
        selected = policy.select(owner="u", user_profile=ExecutionProfile.DEVELOPMENT, task_profile=ExecutionProfile.POWER)
        self.assertEqual(selected.profile, ExecutionProfile.POWER)
        self.assertEqual(selected.source, "task")

    def test_host_admin_requires_allowlist_and_is_audited(self):
        events = []
        denied = ExecutionPolicy(audit=events.append).select(owner="u", task_profile=ExecutionProfile.HOST_ADMIN, task_id="t")
        self.assertEqual(denied.profile, ExecutionProfile.SAFE)
        self.assertFalse(events[-1].allowed)
        allowed_events = []
        allowed = ExecutionPolicy(host_admin_allowlist=frozenset({"admin"}), audit=allowed_events.append).select(
            owner="admin", task_profile=ExecutionProfile.HOST_ADMIN, task_id="t")
        self.assertEqual(allowed.profile, ExecutionProfile.HOST_ADMIN)
        self.assertTrue(allowed_events[-1].allowed)

    def test_all_escalations_are_audited(self):
        events = []
        ExecutionPolicy(audit=events.append).select(owner="u", user_profile=ExecutionProfile.DEVELOPMENT)
        self.assertEqual(events[-1].requested, ExecutionProfile.DEVELOPMENT)

    def test_secrets_are_not_exposed_to_execution_environment(self):
        clean = ExecutionPolicy.sanitize_environment({
            "PATH": "/bin", "BOT_TOKEN": "x", "PASSWORD": "y", "NORMAL_SETTING": "ok", "API_KEY": "z"})
        self.assertEqual(clean, {"PATH": "/bin", "NORMAL_SETTING": "ok"})
