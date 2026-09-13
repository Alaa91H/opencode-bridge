from __future__ import annotations

import unittest

from resource_monitor import HostResourcePolicy, ResourceSnapshot


class FixedPolicy(HostResourcePolicy):
    def __init__(self, value: ResourceSnapshot) -> None:
        super().__init__()
        self.value = value

    def snapshot(self, force: bool = False) -> ResourceSnapshot:
        return self.value


class ResourcePolicyTests(unittest.TestCase):
    def sample(self, total=4096, available=3072, cpus=4, load1=0.5, disk=50.0, psi=0.0):
        return ResourceSnapshot(total, available, 1024, 1024, cpus, load1, disk, psi)

    def test_healthy_host_keeps_requested_workers(self) -> None:
        self.assertEqual(FixedPolicy(self.sample()).decide(4).allowed_workers, 4)

    def test_small_memory_host_uses_one_worker(self) -> None:
        self.assertEqual(FixedPolicy(self.sample(total=1024, available=700)).decide(4).allowed_workers, 1)

    def test_low_available_memory_uses_one_worker(self) -> None:
        self.assertEqual(FixedPolicy(self.sample(available=300)).decide(6).allowed_workers, 1)

    def test_high_cpu_load_uses_one_worker(self) -> None:
        self.assertEqual(FixedPolicy(self.sample(cpus=2, load1=4.5)).decide(6).allowed_workers, 1)

    def test_memory_stalls_reduce_parallelism(self) -> None:
        self.assertEqual(FixedPolicy(self.sample(psi=3.0)).decide(6).allowed_workers, 2)


if __name__ == "__main__":
    unittest.main()
