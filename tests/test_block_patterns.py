"""Tests for command block patterns (build and hardline prohibitions)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from block_patterns import check_build, check_command, check_hardline


class BuildPatternTests(unittest.TestCase):
    def test_blocks_npm_build(self) -> None:
        self.assertIsNotNone(check_build("npm run build"))
        self.assertIsNotNone(check_build("npm run prod"))

    def test_blocks_pip_install(self) -> None:
        self.assertIsNotNone(check_build("pip install requests"))
        self.assertIsNotNone(check_build("pip3 install -r requirements.txt"))
        self.assertIsNotNone(check_build("python -m pip install flask"))

    def test_blocks_cargo_build(self) -> None:
        self.assertIsNotNone(check_build("cargo build --release"))

    def test_blocks_go_build(self) -> None:
        self.assertIsNotNone(check_build("go build ./..."))

    def test_blocks_make_and_cmake(self) -> None:
        self.assertIsNotNone(check_build("make"))
        self.assertIsNotNone(check_build("cmake .."))

    def test_blocks_docker_build(self) -> None:
        self.assertIsNotNone(check_build("docker build -t myapp ."))
        self.assertIsNotNone(check_build("docker-compose build"))

    def test_blocks_gradle_and_maven(self) -> None:
        self.assertIsNotNone(check_build("./gradlew build"))
        self.assertIsNotNone(check_build("mvn install"))

    def test_allows_normal_prose(self) -> None:
        self.assertIsNone(check_build("please make a plan for the feature"))
        self.assertIsNone(check_build("build the user interface in Figma"))
        self.assertIsNone(check_build("running a production service is fine"))


class HardlinePatternTests(unittest.TestCase):
    def test_blocks_rm_system_path(self) -> None:
        self.assertIsNotNone(check_hardline("rm -rf /"))
        self.assertIsNotNone(check_hardline("rm -rf /home/*"))
        self.assertIsNotNone(check_hardline("rm -rf /etc/*"))

    def test_blocks_shutdown_reboot(self) -> None:
        self.assertIsNotNone(check_hardline("shutdown now"))
        self.assertIsNotNone(check_hardline("reboot"))
        self.assertIsNotNone(check_hardline("sudo systemctl poweroff"))
        self.assertIsNotNone(check_hardline("halt"))

    def test_blocks_mkfs_and_dd(self) -> None:
        self.assertIsNotNone(check_hardline("mkfs.ext4 /dev/sda1"))
        self.assertIsNotNone(check_hardline("dd if=/dev/zero of=/dev/sda"))

    def test_blocks_fork_bomb(self) -> None:
        self.assertIsNotNone(check_hardline(":(){ :|:& };:"))

    def test_allows_safe_rm(self) -> None:
        self.assertIsNone(check_hardline("rm /tmp/foo.txt"))
        self.assertIsNone(check_hardline("rm ./local-file.txt"))


class CheckCommandTests(unittest.TestCase):
    def test_hardline_is_dangerous(self) -> None:
        reason, blocked, dangerous = check_command("rm -rf /")
        self.assertTrue(blocked)
        self.assertTrue(dangerous)
        self.assertIsNotNone(reason)

    def test_build_is_blocked_but_not_dangerous(self) -> None:
        reason, blocked, dangerous = check_command("npm run build")
        self.assertTrue(blocked)
        self.assertFalse(dangerous)
        self.assertIsNotNone(reason)

    def test_safe_command_passes(self) -> None:
        reason, blocked, dangerous = check_command("list the files in /tmp")
        self.assertFalse(blocked)
        self.assertFalse(dangerous)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
