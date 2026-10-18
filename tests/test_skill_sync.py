"""The agent's skills must arrive from the repository automatically and safely.

Skills land inside the agent's own instruction path, so a repository is untrusted
input: a broken or hostile skill has to be refused before it is published, an
empty repository must never delete what the agent already has, and a sync that
fails must leave the installed library exactly as it was. These tests pin that
contract, and the fact that a changed skill asks for an agent-server reload,
because the server reads that directory only at startup.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bridge.services import skill_sync
from bridge.services.host_control import CommandResult
from bridge.services.skill_sync import (
    DEFAULT_SOURCE,
    MAX_SKILLS,
    Skill,
    SkillSyncError,
    SkillSyncService,
    collect_skills,
    validate_skill,
)

SKILL = """---
name: {name}
description: {description}
---

# {name}

Body.
"""


def write_skill(root: Path, name: str, description: str = "Does a thing well.") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        SKILL.format(name=name, description=description), encoding="utf-8",
    )
    return directory


class _FakeGit:
    """A git stand-in that copies a fixture tree instead of cloning."""

    def __init__(self, fixture: Path | None, *, code: int = 0, commit: str = "abc1234") -> None:
        self.fixture = fixture
        self.code = code
        self.commit = commit
        self.calls: list[list[str]] = []

    def _places_a_tree(self, argv: list[str]) -> bool:
        """True only for the commands whose last argument is a directory to fill."""
        if len(argv) < 2:
            return False
        return "clone" in argv or "worktree" in argv

    async def __call__(self, argv: list[str], *, timeout: float = 60.0) -> CommandResult:
        self.calls.append(list(argv))
        if self.fixture is None:
            return CommandResult(code=self.code, output="")
        if not self._places_a_tree(argv):
            # Copying for every argv invented directories in the working
            # directory: `git rev-parse HEAD` produced a folder called HEAD, and
            # the systemctl restart produced one named after the service. Running
            # the suite inside the deployment left debris there, and the
            # acceptance sweep then reported the deployment as a dirty tree.
            return CommandResult(code=0, output=self.commit)
        target = Path(argv[-1])
        import shutil

        shutil.copytree(self.fixture, target, dirs_exist_ok=True)
        return CommandResult(code=0, output=self.commit)


class ValidationTests(unittest.TestCase):
    def test_a_well_formed_skill_is_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            skill = validate_skill(write_skill(Path(tmp), "good-skill"))
            self.assertEqual(skill.name, "good-skill")
            self.assertEqual(skill.description, "Does a thing well.")
            self.assertTrue(skill.digest)

    def test_a_name_that_is_not_portable_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = write_skill(Path(tmp), "Bad_Name")
            with self.assertRaises(SkillSyncError):
                validate_skill(directory)

    def test_a_name_that_disagrees_with_its_folder_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = write_skill(Path(tmp), "actual")
            (directory / "SKILL.md").write_text(
                SKILL.format(name="other", description="x"), encoding="utf-8",
            )
            with self.assertRaises(SkillSyncError):
                validate_skill(directory)

    def test_a_missing_description_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "nameless"
            directory.mkdir()
            (directory / "SKILL.md").write_text("---\nname: nameless\n---\n\nBody\n", encoding="utf-8")
            with self.assertRaises(SkillSyncError):
                validate_skill(directory)

    def test_a_missing_manifest_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "empty-skill").mkdir()
            with self.assertRaises(SkillSyncError):
                validate_skill(Path(tmp) / "empty-skill")

    def test_a_symlink_inside_a_skill_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = write_skill(Path(tmp), "linked")
            try:
                (directory / "reference.md").symlink_to("/etc/hostname")
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(SkillSyncError):
                validate_skill(directory)

    def test_collecting_reports_the_refusals_instead_of_hiding_them(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root, "good-skill")
            (root / "Bad_Name").mkdir()
            (root / "Bad_Name" / "SKILL.md").write_text("x", encoding="utf-8")
            accepted, rejected = collect_skills(root)
            self.assertEqual([skill.name for skill in accepted], ["good-skill"])
            self.assertEqual(len(rejected), 1)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, tmp: str, fixture: Path | None, **kwargs: object) -> tuple[SkillSyncService, _FakeGit]:
        runtime = Path(tmp) / "runtime"
        target = Path(tmp) / "skills"
        git = _FakeGit(fixture, **kwargs)  # type: ignore[arg-type]
        service = SkillSyncService(
            runtime, target_dir=target, source=DEFAULT_SOURCE, runner=git,
        )
        return service, git

    async def test_a_first_sync_publishes_and_asks_for_a_reload(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "research-notes")
            service, _git = self._service(tmp, Path(upstream))
            result = await service.sync()
            self.assertEqual(result.status, "synced")
            self.assertEqual(result.installed, ("research-notes",))
            self.assertTrue(result.needs_reload)
            installed = service.installed_skills()
            self.assertEqual([skill.name for skill in installed], ["research-notes"])

    async def test_a_second_sync_with_no_change_does_not_ask_again(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "research-notes")
            service, _git = self._service(tmp, Path(upstream))
            await service.sync()
            service.clear_reload()
            again = await service.sync()
            self.assertEqual(again.status, "unchanged")
            self.assertFalse(again.needs_reload)

    async def test_a_skill_removed_upstream_is_removed_here(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "keep-me")
            service, _git = self._service(tmp, Path(upstream))
            await service.sync()
            import shutil

            shutil.rmtree(Path(upstream) / "keep-me")
            write_skill(Path(upstream), "new-one")
            result = await service.sync()
            self.assertEqual(result.removed, ("keep-me",))
            self.assertEqual([s.name for s in service.installed_skills()], ["new-one"])

    async def test_an_empty_repository_never_deletes_the_installed_library(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "keep-me")
            service, _git = self._service(tmp, Path(upstream))
            await service.sync()
            import shutil

            shutil.rmtree(Path(upstream) / "keep-me")
            result = await service.sync()
            self.assertEqual(result.status, "empty")
            self.assertEqual([s.name for s in service.installed_skills()], ["keep-me"])

    async def test_an_unreachable_repository_leaves_everything_alone(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "skills"
            target.mkdir()
            write_skill(target, "already-here")
            service, _git = self._service(tmp, None, code=128)
            result = await service.sync()
            self.assertEqual(result.status, "failed")
            self.assertIn("already-here", [skill.name for skill in service.installed_skills()])
            self.assertIn("skills repository", result.message)

    async def test_a_refused_skill_never_reaches_the_agent(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "good-skill")
            (Path(upstream) / "Bad_Name").mkdir()
            (Path(upstream) / "Bad_Name" / "SKILL.md").write_text("x", encoding="utf-8")
            service, _git = self._service(tmp, Path(upstream))
            result = await service.sync()
            self.assertEqual([s.name for s in service.installed_skills()], ["good-skill"])
            self.assertEqual(len(result.rejected), 1)

    async def test_a_hand_written_skill_is_never_deleted_by_a_sync(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "managed")
            service, _git = self._service(tmp, Path(upstream))
            await service.sync()
            write_skill(service.target, "owner-made")
            await service.sync()
            names = [skill.name for skill in service.installed_skills()]
            self.assertIn("owner-made", names)

    async def test_the_state_file_records_what_published(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "tracked-skill")
            service, _git = self._service(tmp, Path(upstream))
            await service.sync()
            state = json.loads(service.state_path.read_text(encoding="utf-8"))
            self.assertEqual(list(state["managed"]), ["tracked-skill"])
            self.assertEqual(state["source"], DEFAULT_SOURCE)
            if os.name == "posix":
                self.assertEqual(state["managed"]["tracked-skill"][:8],
                                 validate_skill(Path(upstream) / "tracked-skill").digest[:8])

    async def test_reload_only_happens_when_a_reload_is_pending(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "reload-me")
            service, git = self._service(tmp, Path(upstream))
            await service.sync()
            ok, _reason = await service.reload_agent_server()
            self.assertTrue(ok)
            self.assertIn("opencode-serve.service", git.calls[-1])
            self.assertFalse(service.status()["needs_reload"])


class SkillModelTests(unittest.TestCase):
    def test_a_skill_value_carries_its_digest_and_size(self) -> None:
        with TemporaryDirectory() as tmp:
            skill = validate_skill(write_skill(Path(tmp), "measured"))
            self.assertIsInstance(skill, Skill)
            self.assertGreater(skill.size_bytes, 0)


class LoopHygieneTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_syncs_never_overlap(self) -> None:
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "one-skill")
            runtime = Path(tmp) / "runtime"
            target = Path(tmp) / "skills"
            git = _FakeGit(Path(upstream))
            service = SkillSyncService(runtime, target_dir=target, runner=git)
            results = await asyncio.gather(service.sync(), service.sync())
            self.assertTrue(all(result.ok for result in results))


class TheFakeWritesNothingIntoTheWorkingDirectoryTests(unittest.IsolatedAsyncioTestCase):
    """Found live: the suite left debris inside the deployment tree.

    The git stand-in copied its fixture for every command it was handed, so
    ``git rev-parse HEAD`` created a directory called ``HEAD`` and the systemctl
    restart created one named after the service. Running the tests where the
    product lives therefore littered the deployment, and the acceptance sweep
    correctly reported the release as a dirty working tree.
    """

    async def test_a_full_sync_and_reload_touch_nothing_here(self) -> None:
        before = set(os.listdir("."))
        with TemporaryDirectory() as upstream, TemporaryDirectory() as tmp:
            write_skill(Path(upstream), "no-debris")
            service, git = SyncTests()._service(tmp, Path(upstream))
            result = await service.sync()
            self.assertTrue(result.ok)
            await service.reload_agent_server()
            self.assertTrue(any("opencode-serve.service" in call for call in git.calls))
        after = set(os.listdir("."))
        self.assertEqual(after - before, set())

    async def test_only_clone_like_commands_place_a_tree(self) -> None:
        git = _FakeGit(Path("/nonexistent-fixture"))
        self.assertTrue(git._places_a_tree(["git", "clone", "--depth", "1", "url", "/tmp/x"]))
        self.assertFalse(git._places_a_tree(["git", "-C", "/tmp/x", "rev-parse", "HEAD"]))
        self.assertFalse(git._places_a_tree(["systemctl", "--user", "restart", "opencode-serve.service"]))


class PortableCollectionLayoutTests(unittest.TestCase):
    """A collection that serves many agents keeps skills in ``skills/``.

    Found live: the repository grew from three skills at the root to 84 inside
    ``skills/``, mirrored for every supported tool. The shipped collector found
    nothing — ``accepted=0``, with ``skills``, ``docs``, ``spec``, ``tests`` and
    ``scripts`` all refused as missing a SKILL.md — so the agent kept running on
    three stale skills and reported itself as in sync.
    """

    def test_skills_are_collected_from_the_nested_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root / "skills", "alpha-skill")
            write_skill(root / "skills", "beta-skill")
            accepted, rejected = collect_skills(root)
            self.assertEqual([skill.name for skill in accepted], ["alpha-skill", "beta-skill"])
            self.assertEqual(rejected, ())

    def test_a_root_level_repository_still_works(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root, "solo-skill")
            accepted, _ = collect_skills(root)
            self.assertEqual([skill.name for skill in accepted], ["solo-skill"])

    def test_the_same_skills_are_not_installed_eight_times(self) -> None:
        """The mirrors are for other agents; installing them all would be a mess."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mirror in (".agents", ".claude", ".cursor", ".windsurf"):
                write_skill(root / mirror / "skills", "mirrored-skill")
            write_skill(root / "skills", "mirrored-skill")
            accepted, _ = collect_skills(root)
            self.assertEqual([skill.name for skill in accepted], ["mirrored-skill"])

    def test_project_directories_are_not_mistaken_for_skills(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("docs", "spec", "tests", "scripts"):
                (root / name).mkdir()
            write_skill(root / "skills", "real-skill")
            accepted, _ = collect_skills(root)
            self.assertEqual([skill.name for skill in accepted], ["real-skill"])

    def test_the_collection_of_today_is_inside_the_cap(self) -> None:
        self.assertGreaterEqual(MAX_SKILLS, 84)

    def test_a_pathological_repository_is_still_refused(self) -> None:
        self.assertIsInstance(MAX_SKILLS, int)
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            root.mkdir()
            write_skill(root, "measured-skill")
            original = skill_sync.MAX_SKILLS
            skill_sync.MAX_SKILLS = 0
            try:
                with self.assertRaises(SkillSyncError):
                    collect_skills(Path(tmp))
            finally:
                skill_sync.MAX_SKILLS = original


if __name__ == "__main__":
    unittest.main()
