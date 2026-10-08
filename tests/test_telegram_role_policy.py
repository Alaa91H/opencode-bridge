"""Tests for the explicit Telegram role policy."""

import unittest

from bridge.telegram.role_policy import Permission, Role, RolePolicy


class RolePolicyTests(unittest.TestCase):
    def test_unassigned_actor_has_no_permissions(self):
        policy = RolePolicy({})
        for permission in Permission:
            self.assertFalse(policy.allows(42, permission))

    def test_reader_is_read_only(self):
        policy = RolePolicy({1: Role.READER})
        self.assertTrue(policy.allows(1, Permission.READ))
        self.assertFalse(policy.allows(1, Permission.OPERATE))

    def test_operator_cannot_administer(self):
        policy = RolePolicy({1: Role.OPERATOR})
        self.assertTrue(policy.allows(1, Permission.OPERATE))
        self.assertFalse(policy.allows(1, Permission.ADMINISTER))

    def test_admin_cannot_use_owner_only_permission(self):
        policy = RolePolicy({1: Role.ADMIN})
        self.assertTrue(policy.allows(1, Permission.ADMINISTER))
        self.assertFalse(policy.allows(1, Permission.OWNER_ONLY))

    def test_owner_has_all_permissions(self):
        policy = RolePolicy({1: Role.OWNER})
        for permission in Permission:
            self.assertTrue(policy.allows(1, permission))

    def test_invalid_assignments_fail_closed(self):
        policy = RolePolicy({1: "owner", 2: None, 3: Role.OWNER})
        self.assertFalse(policy.allows(1, Permission.READ))
        self.assertFalse(policy.allows(2, Permission.READ))
        self.assertFalse(policy.allows(3, "read"))
        self.assertFalse(policy.allows(True, Permission.READ))
        self.assertFalse(policy.allows(-1, Permission.READ))
