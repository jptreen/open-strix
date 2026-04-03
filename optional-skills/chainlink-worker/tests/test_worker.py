from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from datetime import timedelta
import unittest
from unittest.mock import AsyncMock, Mock

from config import Settings, WorkerConfig
from worker import CommandResult, Poller, Worker, utc_now


class WorkerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.chainlink_cwd = self.root / "chainlink"
        self.chainlink_cwd.mkdir()
        self.repo_a = self.root / "repo-a"
        self.repo_b = self.root / "repo-b"
        self.repo_a.mkdir()
        self.repo_b.mkdir()
        self.settings = Settings(
            chainlink_cwd=self.chainlink_cwd,
            poll_interval_seconds=0.001,
            codex_poll_seconds=0.001,
            max_codex_wait_seconds=1,
            agent_id="backlog-worker",
        )

    async def test_worktree_setup_and_teardown(self) -> None:
        worker = Worker(
            WorkerConfig(
                name="data-lake-ml",
                repo=self.repo_a,
                worktree=True,
                branch_prefix="chainlink/",
            ),
            self.settings,
            prompt_dir=self.root / "prompts",
        )
        worker._run_shell = AsyncMock(return_value=CommandResult(0, "", ""))

        await worker.setup_worktree()

        expected_path = self.repo_a / ".worktrees" / "chainlink-data-lake-ml"
        self.assertEqual(worker.work_dir, expected_path)
        first_call = worker._run_shell.await_args_list[0]
        self.assertEqual(
            first_call.args[0],
            ["git", "worktree", "add", str(expected_path), "-b", "chainlink/data-lake-ml"],
        )
        self.assertEqual(first_call.args[1], self.repo_a)

        expected_path.mkdir(parents=True)
        await worker.teardown_worktree()

        second_call = worker._run_shell.await_args_list[1]
        self.assertEqual(
            second_call.args[0],
            ["git", "worktree", "remove", str(expected_path), "--force"],
        )
        self.assertEqual(second_call.args[1], self.repo_a)

    async def test_poller_routes_by_label_text_and_round_robin(self) -> None:
        worker_one = Worker(
            WorkerConfig("data-lake-ml", self.repo_a, True, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "p1",
        )
        worker_two = Worker(
            WorkerConfig("data-lake-ml-2", self.repo_a, True, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "p2",
        )
        worker_three = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "p3",
        )
        poller = Poller(self.settings, [worker_one, worker_two, worker_three])

        issue_one = {"id": 1, "labels": ["repo:data-lake-ml"], "title": "Fix data-lake-ml loader"}
        issue_two = {"id": 2, "labels": ["repo:data-lake-ml"], "title": "Fix data-lake-ml retry"}
        issue_three = {"id": 3, "labels": [], "title": "Polish open-strix prompts", "description": "Touch open-strix"}

        self.assertIs(poller.route_issue(issue_one), worker_one)
        self.assertIs(poller.route_issue(issue_two), worker_two)
        self.assertIs(poller.route_issue(issue_three), worker_three)

        worker_one.state.current_issue = {"id": 99}
        busy_issue = {"id": 4, "labels": ["repo:data-lake-ml"], "title": "Another data-lake-ml task"}
        self.assertIs(poller.route_issue(busy_issue), worker_two)

    async def test_stale_claim_reaper_reclaims_unowned_issue(self) -> None:
        worker = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "prompts",
        )
        worker.work_dir = self.repo_b
        poller = Poller(self.settings, [worker])
        poller._run_shell = AsyncMock(
            side_effect=[
                CommandResult(0, json.dumps([{"id": 9, "labels": ["in-progress"]}]), ""),
                CommandResult(0, "", ""),
            ]
        )

        await poller.reap_stale_claims()

        self.assertEqual(len(poller._run_shell.await_args_list), 2)
        reclaim_call = poller._run_shell.await_args_list[1]
        self.assertEqual(
            reclaim_call.args[0][-3:],
            ["unlabel", "9", "in-progress"],
        )

    async def test_stale_claim_reaper_requests_abort_for_owned_stale_issue(self) -> None:
        stale_settings = Settings(
            chainlink_cwd=self.chainlink_cwd,
            poll_interval_seconds=0.001,
            codex_poll_seconds=0.001,
            max_codex_wait_seconds=1,
            agent_id="backlog-worker",
        )
        worker = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            stale_settings,
            prompt_dir=self.root / "prompts",
        )
        worker.work_dir = self.repo_b
        worker.state.current_issue = {"id": 11}
        worker.state.session_name = "issue-11"
        worker.state.claimed_at = utc_now() - timedelta(seconds=5)
        worker.request_abort = Mock()

        poller = Poller(stale_settings, [worker])
        poller._run_shell = AsyncMock(
            return_value=CommandResult(0, json.dumps([{"id": 11, "labels": ["in-progress"]}]), "")
        )

        await poller.reap_stale_claims()

        worker.request_abort.assert_called_once_with("stale_claim")
        self.assertEqual(len(poller._run_shell.await_args_list), 1)

    async def test_full_issue_lifecycle_claim_prompt_review_close(self) -> None:
        issue = {
            "id": 7,
            "status": "open",
            "title": "Refactor worker",
            "description": "Make it async.",
            "labels": ["repo:open-strix"],
            "comments": [],
        }
        issue_after_initial = dict(issue, comments=[], labels=["ready-for-review"])
        issue_with_feedback = dict(
            issue,
            comments=[{"id": 1, "kind": "human", "content": "Please add regression coverage"}],
            labels=["ready-for-review"],
        )
        issue_after_review = dict(
            issue,
            comments=[{"id": 1, "kind": "human", "content": "Please add regression coverage"}],
            labels=["ready-for-review"],
        )
        issue_approved = dict(
            issue,
            comments=[
                {"id": 1, "kind": "human", "content": "Please add regression coverage"},
                {"id": 2, "kind": "resolution", "content": "APPROVED"},
            ],
            labels=["ready-for-review"],
        )

        worker = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "prompts",
        )
        worker.work_dir = self.repo_b

        expected = [
            (("label", "7", "in-progress"), CommandResult(0, "", "")),
            (("session", "work", "7"), CommandResult(0, "", "")),
            (("sessions", "show", "issue-7"), CommandResult(1, "", "missing")),
            (("sessions", "new", "--name", "issue-7"), CommandResult(0, "", "")),
            (("set-mode", "-s", "issue-7", "full-access"), CommandResult(0, "", "")),
            (("sessions", "show", "issue-7"), CommandResult(0, "historyEntries: 0\nclosed: no\n", "")),
            (("-s", "issue-7", "--no-wait", "-f"), CommandResult(0, "", "")),
            (("sessions", "show", "issue-7"), CommandResult(0, "historyEntries: 1\nclosed: no\n", "")),
            (("sessions", "read", "issue-7", "--tail", "1"), CommandResult(0, "Implemented the async flow", "")),
            (("label", "7", "ready-for-review"), CommandResult(0, "", "")),
            (("unlabel", "7", "in-progress"), CommandResult(0, "", "")),
            (("show", "7", "--json"), CommandResult(0, json.dumps(issue_after_initial), "")),
            (("show", "7", "--json"), CommandResult(0, json.dumps(issue_with_feedback), "")),
            (("label", "7", "in-progress"), CommandResult(0, "", "")),
            (("unlabel", "7", "ready-for-review"), CommandResult(0, "", "")),
            (("sessions", "show", "issue-7"), CommandResult(0, "historyEntries: 1\nclosed: no\n", "")),
            (("-s", "issue-7", "--no-wait", "-f"), CommandResult(0, "", "")),
            (("sessions", "show", "issue-7"), CommandResult(0, "historyEntries: 2\nclosed: no\n", "")),
            (("sessions", "read", "issue-7", "--tail", "1"), CommandResult(0, "Added regression coverage", "")),
            (("label", "7", "ready-for-review"), CommandResult(0, "", "")),
            (("unlabel", "7", "in-progress"), CommandResult(0, "", "")),
            (("show", "7", "--json"), CommandResult(0, json.dumps(issue_after_review), "")),
            (("show", "7", "--json"), CommandResult(0, json.dumps(issue_approved), "")),
            (("close", "7"), CommandResult(0, "", "")),
            (("sessions", "close", "issue-7"), CommandResult(0, "", "")),
        ]

        async def fake_run_shell(command: list[str], cwd: Path, timeout: int = 60, *, check: bool = True) -> CommandResult:
            self.assertTrue(expected, f"unexpected command: {command}")
            prefix, result = expected.pop(0)
            if command[:3] == [str(Path.home() / ".cargo" / "bin" / "chainlink"), *prefix[:2]]:
                actual = tuple(command[1:1 + len(prefix)])
            elif command[:3] == ["npx", "acpx", "codex"]:
                actual = tuple(command[3:3 + len(prefix)])
            else:
                self.fail(f"unexpected executable: {command}")
            self.assertEqual(actual, prefix)
            return result

        worker._run_shell = AsyncMock(side_effect=fake_run_shell)

        await worker.process_issue(issue)

        self.assertEqual(expected, [])
        self.assertIsNone(worker.state.issue_id)
        self.assertEqual(worker.state.phase, "idle")

    async def test_graceful_shutdown_releases_current_issue(self) -> None:
        worker = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "prompts",
        )
        worker.work_dir = self.repo_b
        release_mock = AsyncMock()
        worker.release_current_issue = release_mock
        started = asyncio.Event()

        async def blocking_process(issue: dict[str, int]) -> None:
            worker.state.current_issue = issue
            worker.state.phase = "implementing"
            started.set()
            await asyncio.Event().wait()

        worker.process_issue = AsyncMock(side_effect=blocking_process)
        task = asyncio.create_task(worker.run())
        await worker.enqueue_issue({"id": 21})
        await started.wait()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        release_mock.assert_awaited_once_with("shutdown")

    async def test_worker_queue_processes_multiple_issues_in_sequence(self) -> None:
        worker = Worker(
            WorkerConfig("open-strix", self.repo_b, False, "chainlink/"),
            self.settings,
            prompt_dir=self.root / "prompts",
        )
        worker.work_dir = self.repo_b
        processed: list[int] = []
        done = asyncio.Event()

        async def record_issue(issue: dict[str, int]) -> None:
            processed.append(int(issue["id"]))
            if len(processed) == 2:
                done.set()

        worker.process_issue = AsyncMock(side_effect=record_issue)
        task = asyncio.create_task(worker.run())
        await worker.enqueue_issue({"id": 31})
        await worker.enqueue_issue({"id": 32})
        await asyncio.wait_for(done.wait(), timeout=1)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(processed, [31, 32])
