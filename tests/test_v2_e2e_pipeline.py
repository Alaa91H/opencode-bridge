import unittest
from dataclasses import dataclass


@dataclass
class TelegramUpdate:
    update_id: int
    text: str


class FakeQueue:
    def __init__(self):
        self.items = []

    async def enqueue(self, prompt):
        task = {"id": f"task-{len(self.items)+1}", "prompt": prompt}
        self.items.append(task)
        return task


class FakeOpenCode:
    async def execute(self, prompt):
        return f"result:{prompt}"


class FakeOutput:
    def __init__(self):
        self.sent = []

    async def deliver(self, task_id, result):
        self.sent.append((task_id, result))


async def e2e(update, queue, opencode, output):
    task = await queue.enqueue(update.text)
    result = await opencode.execute(task["prompt"])
    await output.deliver(task["id"], result)
    return task["id"]


class E2EPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_mock_to_queue_opencode_mock_to_output(self):
        queue = FakeQueue()
        opencode = FakeOpenCode()
        output = FakeOutput()
        task_id = await e2e(TelegramUpdate(1, "build"), queue, opencode, output)
        self.assertEqual(queue.items[0]["prompt"], "build")
        self.assertEqual(output.sent, [(task_id, "result:build")])
