import unittest

from scripts.load_soak import run


class LoadSoakTests(unittest.IsolatedAsyncioTestCase):
    async def test_thousands_tasks_hundreds_schedules_parallel_owners(self):
        result = await run(
            tasks=2000, schedules=200, owners=50,
            payload_bytes=4 * 1024 * 1024 * 1024, slow_ms=0, duration_seconds=0)
        self.assertGreaterEqual(result["tasks"], 2000)
        self.assertLess(result["peak_memory_bytes"], 16 * 1024 * 1024)

    async def test_slow_backend_harness(self):
        result = await run(
            tasks=20, schedules=5, owners=4,
            payload_bytes=1024 * 1024, slow_ms=.1, duration_seconds=0)
        self.assertEqual(result["tasks"], 20)
