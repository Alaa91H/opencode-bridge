import unittest

from bridge.domain.tasks.fair_queue import FairQueuePolicy, FairTask, Priority
from bridge.domain.tasks.resource_scheduler import ResourceCost


class PriorityFairnessTests(unittest.TestCase):
    def setUp(self):
        self.policy = FairQueuePolicy(aging_seconds=10, interactive_boost=5, owner_penalty=3, resource_penalty=.1)

    def test_priority_order(self):
        now = 100
        tasks = [FairTask("l", "a", Priority.LOW, now), FairTask("u", "b", Priority.URGENT, now)]
        self.assertEqual(self.policy.choose(tasks, now=now).task_id, "u")

    def test_aging_prevents_starvation(self):
        now = 1000
        old = FairTask("old", "a", Priority.LOW, 0)
        new = FairTask("new", "b", Priority.URGENT, now)
        self.assertEqual(self.policy.choose([new, old], now=now).task_id, "old")

    def test_owner_with_running_work_is_penalized(self):
        now = 100
        a = FairTask("a", "owner-a", Priority.NORMAL, now)
        b = FairTask("b", "owner-b", Priority.NORMAL, now)
        chosen = self.policy.choose([a, b], now=now, owner_running={"owner-a": 2, "owner-b": 0})
        self.assertEqual(chosen.task_id, "b")

    def test_resource_weight_prefers_lighter_equal_priority_work(self):
        now = 100
        heavy = FairTask("heavy", "a", Priority.NORMAL, now, cost=ResourceCost(cpu=8, ram_mb=8000))
        light = FairTask("light", "b", Priority.NORMAL, now, cost=ResourceCost(cpu=.2, ram_mb=100))
        self.assertEqual(self.policy.choose([heavy, light], now=now).task_id, "light")

    def test_interactive_not_blocked_by_equal_scheduled_heavy_task(self):
        now = 100
        scheduled = FairTask("schedule", "a", Priority.NORMAL, now, scheduled=True, cost=ResourceCost(cpu=4, ram_mb=4000))
        interactive = FairTask("chat", "b", Priority.NORMAL, now, interactive=True, cost=ResourceCost(cpu=.1))
        self.assertEqual(self.policy.choose([scheduled, interactive], now=now).task_id, "chat")
