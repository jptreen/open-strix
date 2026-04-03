from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from config import DEFAULT_BRANCH_PREFIX, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_new_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [settings]
                    chainlink_cwd = "/tmp/chainlink"
                    poll_interval_seconds = 15
                    codex_poll_seconds = 5
                    max_codex_wait_seconds = 900
                    agent_id = "backlog-worker"
                    rules_dir = "/tmp/rules"

                    [workers.data-lake-ml]
                    repo = "/tmp/repos/data-lake-ml"
                    worktree = true
                    branch_prefix = "chainlink/"

                    [workers.data-lake-ml-2]
                    repo = "/tmp/repos/data-lake-ml"
                    worktree = true
                    branch_prefix = "chainlink/"

                    [workers.open-strix]
                    repo = "/tmp/repos/open-strix"
                    worktree = false
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.settings.chainlink_cwd, Path("/tmp/chainlink"))
            self.assertEqual(config.settings.poll_interval_seconds, 15)
            self.assertEqual(config.settings.codex_poll_seconds, 5)
            self.assertEqual(config.settings.max_codex_wait_seconds, 900)
            self.assertEqual(config.settings.agent_id, "backlog-worker")
            self.assertEqual(config.settings.rules_dir, Path("/tmp/rules"))
            self.assertEqual([worker.name for worker in config.workers], ["data-lake-ml", "data-lake-ml-2", "open-strix"])
            self.assertTrue(config.workers[0].worktree)
            self.assertFalse(config.workers[2].worktree)
            self.assertEqual(config.workers[0].branch_prefix, "chainlink/")

    def test_load_config_uses_defaults_for_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [settings]
                    chainlink_cwd = "/tmp/chainlink"

                    [workers.open-strix]
                    repo = "/tmp/repos/open-strix"
                    """
                ).strip(),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.settings.chainlink_cwd, Path("/tmp/chainlink"))
            self.assertEqual(config.settings.poll_interval_seconds, 30)
            self.assertEqual(config.settings.codex_poll_seconds, 10)
            self.assertEqual(config.settings.max_codex_wait_seconds, 1800)
            self.assertEqual(config.settings.agent_id, "backlog-worker")
            self.assertIsNone(config.settings.rules_dir)
            self.assertEqual(len(config.workers), 1)
            self.assertFalse(config.workers[0].worktree)
            self.assertEqual(config.workers[0].branch_prefix, DEFAULT_BRANCH_PREFIX)

    def test_load_config_returns_defaults_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(Path(tmpdir) / "missing.toml")

            self.assertEqual(config.settings.poll_interval_seconds, 30)
            self.assertEqual(config.workers, [])

    def test_load_config_requires_worker_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [workers.open-strix]
                    worktree = false
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing required field 'repo'"):
                load_config(config_path)
