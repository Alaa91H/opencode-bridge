from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bridge.domain.policies import RequestGuard, RequestRejected
from bridge.domain.schedules import format_interval, parse_interval_seconds, parse_utc_datetime, split_pipe_args
from bridge.services.schedule_service import ScheduleNotFound, ScheduleService

UTC = timezone.utc


class FakeScheduleRepository:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], SimpleNamespace] = {}
        self.next_id = 1
        self.enqueued: list[SimpleNamespace] = []

    async def create_scheduled_job(
        self, owner_id, chat_id, name, prompt, next_run_at,
        repeat_seconds=None, timezone_name="UTC",
    ):
        key = (owner_id, name.casefold())
        if key in self.jobs:
            raise ValueError("duplicate")
        job = SimpleNamespace(
            id=self.next_id,
            owner_id=owner_id,
            chat_id=chat_id,
            name=name,
            prompt=prompt,
            enabled=True,
            next_run_at=next_run_at,
            repeat_seconds=repeat_seconds,
            timezone_name=timezone_name,
        )
        self.next_id += 1
        self.jobs[key] = job
        return job

    async def get_scheduled_job(self, owner_id, name):
        return self.jobs.get((owner_id, name.casefold()))

    async def list_scheduled_jobs(self, owner_id, limit=100):
        return [job for (owner, _), job in self.jobs.items() if owner == owner_id][:limit]

    async def rename_scheduled_job(self, owner_id, name, new_name):
        old = (owner_id, name.casefold())
        job = self.jobs.pop(old, None)
        if job is None:
            return None
        job.name = new_name
        self.jobs[(owner_id, new_name.casefold())] = job
        return job

    async def set_scheduled_job_prompt(self, owner_id, name, prompt):
        job = await self.get_scheduled_job(owner_id, name)
        if job:
            job.prompt = prompt
        return job

    async def append_scheduled_job_prompt(self, owner_id, name, text):
        job = await self.get_scheduled_job(owner_id, name)
        if job:
            job.prompt = job.prompt.rstrip() + "\n" + text.rstrip()
        return job

    async def update_scheduled_job_timing(
        self, owner_id, name, next_run_at, repeat_seconds, timezone_name="UTC",
    ):
        job = await self.get_scheduled_job(owner_id, name)
        if job:
            job.next_run_at = next_run_at
            job.repeat_seconds = repeat_seconds
            job.timezone_name = timezone_name
        return job

    async def set_scheduled_job_enabled(self, owner_id, name, enabled):
        job = await self.get_scheduled_job(owner_id, name)
        if job:
            job.enabled = enabled
        return job

    async def delete_scheduled_job(self, owner_id, name):
        return self.jobs.pop((owner_id, name.casefold()), None) is not None

    async def enqueue_scheduled_job_now(self, owner_id, name, status_message_id=None):
        job = await self.get_scheduled_job(owner_id, name)
        if job is None:
            return None
        task = SimpleNamespace(id=len(self.enqueued) + 1, status_message_id=status_message_id)
        self.enqueued.append(task)
        return task


class ScheduleDomainTests(unittest.TestCase):
    def test_parser_and_formatter_are_framework_independent(self) -> None:
        self.assertEqual(split_pipe_args("a | b", 2), ["a", "b"])
        self.assertEqual(parse_interval_seconds("2h"), 7200)
        self.assertEqual(format_interval(7200), "كل 2h")
        parsed = parse_utc_datetime("2027-01-02 03:04")
        self.assertEqual(parsed.tzinfo, UTC)

    def test_short_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_interval_seconds("1m")


class ScheduleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repo = FakeScheduleRepository()
        self.now = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)
        self.guard = RequestGuard((lambda text: "blocked" if "DENY" in text else None,))
        self.service = ScheduleService(self.repo, self.guard, clock=lambda: self.now)

    async def test_service_has_no_telegram_dependency_and_owns_schedule_rules(self) -> None:
        due = self.now + timedelta(hours=1)
        job = await self.service.create_once("u", 10, "once", "hello", due)
        self.assertEqual(job.repeat_seconds, None)

        recurring = await self.service.create_recurring("u", 10, "daily", "hello", "1d")
        self.assertEqual(recurring.repeat_seconds, 86400)
        self.assertEqual(recurring.next_run_at, self.now + timedelta(days=1))

    async def test_guard_applies_to_create_replace_and_append(self) -> None:
        due = self.now + timedelta(hours=1)
        with self.assertRaises(RequestRejected):
            await self.service.create_once("u", 10, "bad", "DENY this", due)

        await self.service.create_once("u", 10, "ok", "hello", due)
        with self.assertRaises(RequestRejected):
            await self.service.replace_prompt("u", "ok", "DENY replacement")
        with self.assertRaises(RequestRejected):
            await self.service.append_prompt("u", "ok", "DENY append")

    async def test_service_supports_full_management_lifecycle(self) -> None:
        due = self.now + timedelta(hours=1)
        await self.service.create_once("u", 10, "job", "hello", due)

        renamed = await self.service.rename("u", "job", "renamed")
        self.assertEqual(renamed.name, "renamed")

        edited = await self.service.replace_prompt("u", "renamed", "replacement")
        self.assertEqual(edited.prompt, "replacement")

        appended = await self.service.append_prompt("u", "renamed", "more")
        self.assertEqual(appended.prompt, "replacement\nmore")

        later = self.now + timedelta(hours=4)
        changed = await self.service.change_next_run("u", "renamed", later)
        self.assertEqual(changed.next_run_at, later)

        recurring, label = await self.service.change_interval("u", "renamed", "30m")
        self.assertEqual(recurring.repeat_seconds, 1800)
        self.assertEqual(label, "30m")

        once, label = await self.service.change_interval("u", "renamed", "once")
        self.assertIsNone(once.repeat_seconds)
        self.assertEqual(label, "مرة واحدة")

        self.assertFalse((await self.service.pause("u", "renamed")).enabled)
        self.assertTrue((await self.service.resume("u", "renamed")).enabled)

        job, task = await self.service.run_now("u", "renamed", status_message_id=99)
        self.assertEqual(job.name, "renamed")
        self.assertEqual(task.status_message_id, 99)

        await self.service.delete("u", "renamed")
        with self.assertRaises(ScheduleNotFound):
            await self.service.get("u", "renamed")


if __name__ == "__main__":
    unittest.main()
