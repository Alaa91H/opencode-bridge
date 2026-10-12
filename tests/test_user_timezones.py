"""Regression coverage for user's local scheduling time."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime

from bridge.domain.schedules.user_timezones import display_local, local_to_utc, validate_zone


class UserTimezoneTests(unittest.TestCase):
    def test_summer_and_winter_utc_conversion(self):
        self.assertEqual(local_to_utc("2026-07-01 12:00", "Europe/Berlin"),
                         datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
        self.assertEqual(local_to_utc("2026-12-01 12:00", "Europe/Berlin"),
                         datetime(2026, 12, 1, 11, 0, tzinfo=UTC))

    def test_explicit_utc_and_dst_gap(self):
        self.assertEqual(local_to_utc("2026-12-01 12:00 UTC", "Europe/Berlin"),
                         datetime(2026, 12, 1, 12, tzinfo=UTC))
        with self.assertRaises(ValueError):
            local_to_utc("2026-03-29 02:30", "Europe/Berlin")

    def test_arbitrary_iana_zone(self):
        self.assertEqual(validate_zone("America/Sao_Paulo"), "America/Sao_Paulo")
        self.assertIn("CEST", display_local(datetime(2026, 7, 1, 12, tzinfo=UTC),
                                             "Europe/Berlin"))
