import unittest

from bridge.domain.tasks.resource_scheduler import (
    ResourceCapacity,
    ResourceCost,
    ResourcePressure,
    ResourceScheduler,
    ResourceTask,
)


class ResourceSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = ResourceScheduler(ResourceCapacity(cpu=8, ram_mb=16000, disk_mb=100000, io=10, model=4, media=4))

    def test_mixed_workloads_are_packed_across_dimensions(self):
        tasks = [
            ResourceTask("model", ResourceCost(cpu=1, ram_mb=1000, disk_mb=100, io=1, model=3)),
            ResourceTask("media", ResourceCost(cpu=4, ram_mb=4000, disk_mb=10000, io=3, media=3)),
            ResourceTask("small", ResourceCost(cpu=1, ram_mb=500, disk_mb=100, io=.5)),
        ]
        selected = self.scheduler.pack(tasks)
        self.assertEqual({x.task_id for x in selected}, {"model", "media", "small"})

    def test_current_pressure_reduces_admission(self):
        task = ResourceTask("heavy", ResourceCost(cpu=3, ram_mb=2000))
        normal = self.scheduler.pack([task])
        pressured = self.scheduler.pack([task], ResourcePressure(cpu=.8))
        self.assertTrue(normal)
        self.assertFalse(pressured)

    def test_ram_guard_prevents_oom_headroom_exhaustion(self):
        scheduler = ResourceScheduler(ResourceCapacity(8, 1000, 10000), ram_guard=.8)
        selected = scheduler.pack([
            ResourceTask("a", ResourceCost(ram_mb=500)),
            ResourceTask("b", ResourceCost(ram_mb=500)),
        ])
        self.assertEqual(len(selected), 1)

    def test_disk_guard_prevents_disk_exhaustion(self):
        scheduler = ResourceScheduler(ResourceCapacity(8, 1000, 1000), disk_guard=.75)
        selected = scheduler.pack([
            ResourceTask("a", ResourceCost(disk_mb=600)),
            ResourceTask("b", ResourceCost(disk_mb=600)),
        ])
        self.assertEqual(len(selected), 1)

    def test_model_and_media_capacity_are_independent(self):
        tasks = [
            ResourceTask("m1", ResourceCost(model=3)),
            ResourceTask("m2", ResourceCost(model=3)),
            ResourceTask("video", ResourceCost(media=3)),
        ]
        selected = {x.task_id for x in self.scheduler.pack(tasks)}
        self.assertIn("video", selected)
        self.assertEqual(len(selected & {"m1", "m2"}), 1)
