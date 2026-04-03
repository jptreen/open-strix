#!/usr/bin/env python3
"""Async worker loop for the chainlink backlog worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    from config import AppConfig, Settings, WorkerConfig, load_config
    from prompt_builder import build_prompt, build_review_prompt
except ImportError:  # pragma: no cover - import path fallback
    from .config import AppConfig, Settings, WorkerConfig, load_config
    from .prompt_builder import build_prompt, build_review_prompt


CHAINLINK_BIN = Path.home() / ".cargo" / "bin" / "chainlink"
REAP_EVERY_N_POLLS = 10

READY_EXCLUDED_LABELS = frozenset({
    "blocked",
    "blocked-on-tim",
    "in-progress",
    "ready-for-review",
})

APPROVAL_PATTERNS = (
    re.compile(r"^approved\b"),
    re.compile(r"^lgtm\b"),
    re.compile(r"^ship it\b"),
    re.compile(r"^looks good\b"),
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One completed subprocess result."""

    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    """Raised when an external command exits non-zero."""

    def __init__(self, command: list[str], result: CommandResult) -> None:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        super().__init__(
            f"command failed ({result.returncode}): {shell_join(command)} :: {detail}"
        )
        self.command = command
        self.result = result


class IssueAbandoned(RuntimeError):
    """Raised when a worker must release its current issue."""


@dataclass(slots=True)
class WorkerState:
    """In-memory state for one worker's current issue lifecycle."""

    current_issue: dict[str, Any] | None = None
    repo_path: Path | None = None
    session_name: str | None = None
    phase: str = "idle"
    review_rounds: int = 0
    prompt_baseline_history: int | None = None
    claimed_at: datetime | None = None
    prompt_sent_at: datetime | None = None
    ready_for_review_at: datetime | None = None
    last_review_at: datetime | None = None
    last_comment_id: int = 0

    @property
    def issue_id(self) -> int | None:
        if not isinstance(self.current_issue, dict):
            return None
        raw_id = self.current_issue.get("id")
        if raw_id is None:
            return None
        return int(raw_id)

    def clear(self) -> None:
        self.current_issue = None
        self.repo_path = None
        self.session_name = None
        self.phase = "idle"
        self.review_rounds = 0
        self.prompt_baseline_history = None
        self.claimed_at = None
        self.prompt_sent_at = None
        self.ready_for_review_at = None
        self.last_review_at = None
        self.last_comment_id = 0


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def shell_join(parts: list[str | Path]) -> str:
    """Quote a command for logs and errors."""
    return " ".join(shlex.quote(str(part)) for part in parts)


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a chainlink timestamp when available."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AsyncCommandMixin:
    """Shared async subprocess helpers."""

    settings: Settings

    def log(self, event: str, **fields: object) -> None:
        """Emit one structured, human-readable log line."""
        timestamp = utc_now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        parts = [timestamp, f"event={event}"]
        for key, value in fields.items():
            parts.append(f"{key}={self._format_log_value(value)}")
        print(" ".join(parts), flush=True)

    async def _run_shell(
        self,
        command: list[str],
        cwd: Path,
        timeout: int = 60,
        *,
        check: bool = True,
    ) -> CommandResult:
        """Run one subprocess with captured text output."""
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        result = CommandResult(
            returncode=proc.returncode,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
        )
        if result.stderr.strip():
            self.log(
                "command_stderr",
                command=shell_join(command),
                stderr=self._clip_text(result.stderr.strip()),
            )
        if check and result.returncode != 0:
            raise CommandError(command, result)
        return result

    async def _run_chainlink(
        self,
        *parts: str | Path,
        check: bool = True,
        timeout: int = 60,
    ) -> CommandResult:
        """Run one chainlink command."""
        return await self._run_shell(
            [str(CHAINLINK_BIN), *(str(part) for part in parts)],
            self.settings.chainlink_cwd,
            timeout=timeout,
            check=check,
        )

    async def _run_chainlink_json(self, *parts: str | Path) -> Any:
        """Run one chainlink command and parse JSON output."""
        result = await self._run_chainlink(*parts)
        return self._parse_json_output(result.stdout)

    def _parse_json_output(self, stdout: str) -> Any:
        """Parse plain JSON or JSON with warning prefixes."""
        parsed = self._try_parse_json(stdout)
        if parsed is None:
            raise ValueError(f"expected JSON output, got: {stdout!r}")
        return parsed

    def _try_parse_json(self, stdout: str) -> Any | None:
        text = stdout.strip()
        if not text:
            return None
        for candidate in (text, self._json_suffix(text, "{"), self._json_suffix(text, "[")):
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _json_suffix(self, text: str, marker: str) -> str | None:
        index = text.find(marker)
        if index == -1:
            return None
        return text[index:]

    def _parse_ready_issue_ids(self, stdout: str) -> list[int]:
        issue_ids: list[int] = []
        for line in stdout.splitlines():
            match = re.match(r"^\s*#(\d+)\b", line)
            if match:
                issue_ids.append(int(match.group(1)))
        return issue_ids

    def _parse_key_value_output(self, stdout: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for line in stdout.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed

    def _coerce_issue_id(self, item: Any) -> int | None:
        if isinstance(item, dict):
            raw_id = item.get("id") or item.get("issue_id") or item.get("number")
        else:
            raw_id = item
        if raw_id is None:
            return None
        return int(raw_id)

    def _max_comment_id(self, issue: dict[str, Any]) -> int:
        comments = issue.get("comments") or []
        return max(
            (int(comment.get("id") or 0) for comment in comments if isinstance(comment, dict)),
            default=0,
        )

    def _extract_assignee(self, issue: dict[str, Any]) -> str:
        for key in ("assigned_to", "assignee", "assigned_agent", "claimed_by", "agent_id"):
            value = issue.get(key)
            if isinstance(value, dict):
                candidate = value.get("id") or value.get("name") or value.get("agent_id")
            else:
                candidate = value
            if candidate:
                return str(candidate).strip()
        return ""

    def _format_log_value(self, value: object) -> str:
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        text = str(value)
        if not text or any(character.isspace() for character in text) or "=" in text:
            return json.dumps(text)
        return text

    def _clip_text(self, text: str, limit: int = 240) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3] + "..."


class Worker(AsyncCommandMixin):
    """One async worker bound to one repo checkout or worktree."""

    def __init__(
        self,
        config: WorkerConfig,
        settings: Settings,
        *,
        prompt_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.state = WorkerState()
        self.work_dir: Path | None = None
        self.pending_issue_ids: set[int] = set()
        self.prompt_dir = prompt_dir or (Path(tempfile.gettempdir()) / "chainlink-worker" / config.name)
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self._abort_reason: str | None = None

    @property
    def worktree_path(self) -> Path:
        """Return the worker's dedicated worktree path."""
        return self.config.repo / ".worktrees" / f"chainlink-{self.config.name}"

    @property
    def branch_name(self) -> str:
        """Return the worker's dedicated branch name."""
        return f"{self.config.branch_prefix}{self.config.name}"

    def current_load(self) -> int:
        """Report current queue + in-flight work."""
        return self.queue.qsize() + (1 if self.state.issue_id is not None else 0)

    def owns_issue(self, issue_id: int) -> bool:
        """Check whether this worker owns or has queued the issue."""
        return issue_id in self.pending_issue_ids or self.state.issue_id == issue_id

    def request_abort(self, reason: str) -> None:
        """Ask the current issue to stop and be released."""
        self._abort_reason = reason
        self.log(
            "issue_abort_requested",
            worker=self.config.name,
            issue_id=self.state.issue_id,
            reason=reason,
        )

    async def enqueue_issue(self, issue: dict[str, Any]) -> bool:
        """Queue one issue if it is not already assigned here."""
        issue_id = int(issue["id"])
        if self.owns_issue(issue_id):
            return False
        self.pending_issue_ids.add(issue_id)
        await self.queue.put(issue)
        return True

    async def setup_worktree(self) -> None:
        """Prepare the worker's dedicated checkout once at startup."""
        if not self.config.worktree:
            self.work_dir = self.config.repo
            return

        self.worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.worktree_path.exists():
            await self._run_shell(
                [
                    "git",
                    "worktree",
                    "add",
                    str(self.worktree_path),
                    "-b",
                    self.branch_name,
                ],
                self.config.repo,
            )
        self.work_dir = self.worktree_path

    async def teardown_worktree(self) -> None:
        """Remove the worker's dedicated worktree on shutdown."""
        if not self.config.worktree:
            return
        if self.worktree_path.exists():
            await self._run_shell(
                ["git", "worktree", "remove", str(self.worktree_path), "--force"],
                self.config.repo,
                check=False,
            )

    async def run(self, queue: asyncio.Queue[dict[str, Any]] | None = None) -> None:
        """Process queued issues one at a time until cancelled."""
        active_queue = queue or self.queue
        self.log(
            "worker_start",
            worker=self.config.name,
            repo=self.config.repo,
            work_dir=self.work_dir or self.config.repo,
            poll_seconds=self.settings.poll_interval_seconds,
            codex_poll_seconds=self.settings.codex_poll_seconds,
            agent_id=self.settings.agent_id,
        )
        try:
            while True:
                issue = await active_queue.get()
                issue_id = int(issue["id"])
                try:
                    self.pending_issue_ids.discard(issue_id)
                    await self.process_issue(issue)
                except asyncio.CancelledError:
                    await self.release_current_issue("shutdown")
                    raise
                except Exception as exc:
                    await self.release_current_issue("error")
                    self.log(
                        "worker_error",
                        worker=self.config.name,
                        issue_id=issue_id,
                        phase=self.state.phase,
                        error=str(exc),
                    )
                finally:
                    active_queue.task_done()
        except asyncio.CancelledError:
            self.log("worker_stop", worker=self.config.name, issue_id=self.state.issue_id, phase=self.state.phase)
            raise

    async def process_issue(self, issue: dict[str, Any]) -> None:
        """Claim an issue, run Codex, and drive review to approval."""
        self._abort_reason = None
        issue_id = int(issue["id"])
        session_name = f"issue-{issue_id}"
        work_dir = self._require_work_dir()

        self.state.current_issue = issue
        self.state.repo_path = work_dir
        self.state.session_name = session_name
        self.state.phase = "claiming"
        self.state.review_rounds = 0
        self.state.last_comment_id = self._max_comment_id(issue)

        try:
            self._raise_if_abort_requested()
            await self.claim_issue(issue_id)
            self.state.claimed_at = utc_now()
            self.log(
                "issue_claimed",
                worker=self.config.name,
                issue_id=issue_id,
                session=session_name,
                repo=work_dir,
            )

            rules = self.load_rules()
            prompt = build_prompt(issue, str(work_dir), rules)

            await self.ensure_session(session_name, work_dir)
            baseline = await self.get_history_entries(session_name, work_dir)
            prompt_file = self.write_prompt_file(issue_id, "initial", prompt)
            await self.send_prompt(session_name, work_dir, prompt_file)

            self.state.prompt_baseline_history = baseline
            self.state.prompt_sent_at = utc_now()
            self.state.phase = "implementing"
            self.log(
                "prompt_queued",
                worker=self.config.name,
                issue_id=issue_id,
                session=session_name,
                history_baseline=baseline,
                prompt_file=prompt_file,
            )

            await self._complete_pending_prompt()
            await self._review_loop()
        except IssueAbandoned as exc:
            self.log(
                "issue_abandoned",
                worker=self.config.name,
                issue_id=issue_id,
                reason=str(exc),
            )
            await self.release_current_issue(str(exc))
        finally:
            self._abort_reason = None

    async def get_issue(self, issue_id: int) -> dict[str, Any]:
        """Fetch a full issue payload."""
        data = await self._run_chainlink_json("show", str(issue_id), "--json")
        if not isinstance(data, dict):
            raise ValueError(f"expected issue dict for #{issue_id}, got {type(data).__name__}")
        return data

    async def claim_issue(self, issue_id: int) -> None:
        """Mark the issue in progress and attach the local session."""
        self._raise_if_abort_requested()
        await self._run_chainlink("label", str(issue_id), "in-progress")
        await self._run_chainlink("session", "work", str(issue_id))

    async def mark_ready_for_review(self, issue_id: int) -> None:
        """Flip labels from active work to review."""
        await self._run_chainlink("label", str(issue_id), "ready-for-review")
        await self._run_chainlink("unlabel", str(issue_id), "in-progress", check=False)

    async def mark_in_progress(self, issue_id: int) -> None:
        """Flip labels from review back to active work."""
        await self._run_chainlink("label", str(issue_id), "in-progress")
        await self._run_chainlink("unlabel", str(issue_id), "ready-for-review", check=False)

    async def finalize_issue(self) -> None:
        """Close the issue and close the associated Codex session."""
        issue_id = self._require_issue_id()
        session_name = self._require_session_name()
        work_dir = self._require_work_dir()
        await self._run_chainlink("close", str(issue_id))
        await self._run_codex(work_dir, "sessions", "close", session_name, check=False)
        self.log(
            "issue_closed",
            worker=self.config.name,
            issue_id=issue_id,
            session=session_name,
        )
        self.state.clear()

    async def release_current_issue(self, reason: str) -> None:
        """Best-effort release for cancellation or failure paths."""
        issue_id = self.state.issue_id
        if issue_id is None:
            self.state.clear()
            self._abort_reason = None
            return

        # Clear the abort flag so cleanup commands can still run.
        self._abort_reason = None
        session_name = self.state.session_name
        work_dir = self.work_dir
        phase = self.state.phase

        if phase in {"claiming", "implementing", "addressing-review"}:
            await self._run_chainlink("unlabel", str(issue_id), "in-progress", check=False)

        if session_name and work_dir is not None and phase in {"claiming", "implementing", "addressing-review"}:
            await self._run_codex(work_dir, "sessions", "close", session_name, check=False)

        self.log(
            "issue_released",
            worker=self.config.name,
            issue_id=issue_id,
            phase=phase,
            reason=reason,
        )
        self.state.clear()
        self._abort_reason = None

    async def ensure_session(self, session_name: str, repo_path: Path) -> None:
        """Reuse an open session or create a new one."""
        metadata = await self.get_session_metadata(session_name, repo_path, allow_missing=True)
        if metadata is None or metadata.get("closed") == "yes":
            await self._run_codex(repo_path, "sessions", "new", "--name", session_name)
        await self._run_codex(repo_path, "set-mode", "-s", session_name, "full-access")

    async def send_prompt(self, session_name: str, repo_path: Path, prompt_file: Path) -> None:
        """Queue a prompt file onto an existing session."""
        await self._run_codex(repo_path, "-s", session_name, "--no-wait", "-f", prompt_file)

    async def get_session_metadata(
        self,
        session_name: str,
        repo_path: Path,
        *,
        allow_missing: bool = False,
    ) -> dict[str, str] | None:
        """Read Codex session metadata from `sessions show`."""
        result = await self._run_codex(
            repo_path,
            "sessions",
            "show",
            session_name,
            check=not allow_missing,
        )
        if result.returncode != 0:
            return None
        return self._parse_key_value_output(result.stdout)

    async def get_history_entries(self, session_name: str, repo_path: Path) -> int:
        """Return current history entry count for a Codex session."""
        metadata = await self.get_session_metadata(session_name, repo_path)
        if not metadata:
            return 0
        raw_value = metadata.get("historyEntries", "0") or "0"
        return int(raw_value)

    async def wait_for_codex_completion(
        self,
        session_name: str,
        repo_path: Path,
        baseline_history: int,
    ) -> dict[str, str]:
        """Wait until Codex writes at least one new history entry."""
        deadline = asyncio.get_running_loop().time() + self.settings.max_codex_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            self._raise_if_abort_requested()
            metadata = await self.get_session_metadata(session_name, repo_path)
            history_entries = int((metadata or {}).get("historyEntries", "0") or "0")
            if history_entries > baseline_history:
                return metadata or {}
            await asyncio.sleep(self.settings.codex_poll_seconds)
        raise TimeoutError(
            f"timed out waiting for Codex session {session_name} after "
            f"{self.settings.max_codex_wait_seconds}s"
        )

    async def read_session_tail(self, session_name: str, repo_path: Path) -> str:
        """Read the last history entry for logging."""
        result = await self._run_codex(repo_path, "sessions", "read", session_name, "--tail", "1")
        return result.stdout.strip()

    async def close_session_only(self) -> None:
        """Close only the active Codex session when the issue is already closed."""
        session_name = self.state.session_name
        work_dir = self.work_dir
        if not session_name or work_dir is None:
            return
        abort_reason = self._abort_reason
        self._abort_reason = None
        try:
            await self._run_codex(work_dir, "sessions", "close", session_name, check=False)
        finally:
            self._abort_reason = abort_reason

    async def is_claim_stale(self, issue: dict[str, Any]) -> bool:
        """Check whether the active claim appears dead or too old."""
        if self.state.issue_id != int(issue.get("id") or 0):
            return False

        if self.state.claimed_at is not None:
            age_seconds = (utc_now() - self.state.claimed_at).total_seconds()
            if age_seconds > self.settings.max_codex_wait_seconds:
                return True

        session_name = self.state.session_name
        work_dir = self.work_dir
        if not session_name or work_dir is None:
            return False

        metadata = await self.get_session_metadata(session_name, work_dir, allow_missing=True)
        if metadata is None:
            return True
        return metadata.get("closed") == "yes"

    def load_rules(self) -> list[str]:
        """Load rule files from the configured rules directory."""
        rules_dir = self.settings.rules_dir
        if rules_dir is None or not rules_dir.exists():
            return []

        rules: list[str] = []
        for path in sorted(rules_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".txt"}:
                continue
            rules.append(f"# {path.name}\n\n{path.read_text(encoding='utf-8').strip()}")
        return rules

    def write_prompt_file(self, issue_id: int, name: str, prompt: str) -> Path:
        """Persist the current prompt to a temp file for `codex -f`."""
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-") or "prompt"
        prompt_path = self.prompt_dir / f"issue-{issue_id}-{safe_name}.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        return prompt_path

    def is_approval_comment(self, comment: dict[str, Any]) -> bool:
        """Treat explicit approval phrases as closure signals."""
        content = " ".join(str(comment.get("content") or "").strip().lower().split())
        kind = str(comment.get("kind") or "").strip().lower()
        if "not approved" in content:
            return False
        if kind not in {"resolution", "decision", "note", "human"}:
            return False
        return any(pattern.search(content) for pattern in APPROVAL_PATTERNS)

    async def _run_codex(
        self,
        repo_path: Path,
        *parts: str | Path,
        check: bool = True,
        timeout: int = 60,
    ) -> CommandResult:
        """Run one codex CLI command."""
        self._raise_if_abort_requested()
        return await self._run_shell(
            ["npx", "acpx", "codex", *(str(part) for part in parts)],
            repo_path,
            timeout=timeout,
            check=check,
        )

    async def _complete_pending_prompt(self) -> None:
        """Wait for the in-flight prompt, then move the issue to review."""
        issue_id = self._require_issue_id()
        session_name = self._require_session_name()
        work_dir = self._require_work_dir()
        baseline_history = self.state.prompt_baseline_history
        if baseline_history is None:
            raise ValueError("prompt baseline history is not set")

        metadata = await self.wait_for_codex_completion(session_name, work_dir, baseline_history)
        tail = await self.read_session_tail(session_name, work_dir)

        await self.mark_ready_for_review(issue_id)
        refreshed_issue = await self.get_issue(issue_id)
        self.state.current_issue = refreshed_issue
        self.state.phase = "awaiting_review"
        self.state.prompt_baseline_history = None
        self.state.ready_for_review_at = utc_now()
        self.state.last_comment_id = self._max_comment_id(refreshed_issue)

        self.log(
            "prompt_complete",
            worker=self.config.name,
            issue_id=issue_id,
            session=session_name,
            history_entries=(metadata or {}).get("historyEntries", "0"),
            review_rounds=self.state.review_rounds,
            tail=self._clip_text(tail),
        )

    async def _review_loop(self) -> None:
        """Wait for review, address feedback, and close on approval."""
        issue_id = self._require_issue_id()
        session_name = self._require_session_name()
        work_dir = self._require_work_dir()

        while True:
            self._raise_if_abort_requested()
            issue = await self.get_issue(issue_id)
            self.state.current_issue = issue

            if str(issue.get("status") or "").strip().lower() == "closed":
                self.log("issue_already_closed", worker=self.config.name, issue_id=issue_id)
                await self.close_session_only()
                self.state.clear()
                return

            new_comments = self._new_comments(issue)
            if not new_comments:
                self.log(
                    "awaiting_review",
                    worker=self.config.name,
                    issue_id=issue_id,
                    review_rounds=self.state.review_rounds,
                )
                await asyncio.sleep(self.settings.poll_interval_seconds)
                continue

            approval_comments = [comment for comment in new_comments if self.is_approval_comment(comment)]
            if approval_comments:
                self.log(
                    "review_approved",
                    worker=self.config.name,
                    issue_id=issue_id,
                    comments=len(approval_comments),
                )
                await self.finalize_issue()
                return

            review_comments = [
                str(comment.get("content") or "").strip()
                for comment in new_comments
                if str(comment.get("content") or "").strip()
            ]
            self.state.last_comment_id = self._max_comment_id(issue)
            if not review_comments:
                await asyncio.sleep(self.settings.poll_interval_seconds)
                continue

            self.state.review_rounds += 1
            self.state.last_review_at = utc_now()
            await self.mark_in_progress(issue_id)

            review_prompt = build_review_prompt(issue, review_comments)
            baseline = await self.get_history_entries(session_name, work_dir)
            prompt_file = self.write_prompt_file(
                issue_id,
                f"review-{self.state.review_rounds}",
                review_prompt,
            )
            await self.send_prompt(session_name, work_dir, prompt_file)

            self.state.prompt_baseline_history = baseline
            self.state.prompt_sent_at = utc_now()
            self.state.phase = "addressing-review"
            self.log(
                "review_feedback_queued",
                worker=self.config.name,
                issue_id=issue_id,
                round=self.state.review_rounds,
                comments=len(review_comments),
                prompt_file=prompt_file,
            )

            await self._complete_pending_prompt()

    def _new_comments(self, issue: dict[str, Any]) -> list[dict[str, Any]]:
        comments = issue.get("comments") or []
        return [
            comment
            for comment in comments
            if isinstance(comment, dict) and int(comment.get("id") or 0) > self.state.last_comment_id
        ]

    def _raise_if_abort_requested(self) -> None:
        if self._abort_reason:
            raise IssueAbandoned(self._abort_reason)

    def _require_issue_id(self) -> int:
        issue_id = self.state.issue_id
        if issue_id is None:
            raise ValueError("worker has no active issue")
        return issue_id

    def _require_session_name(self) -> str:
        if not self.state.session_name:
            raise ValueError("worker has no active session")
        return self.state.session_name

    def _require_work_dir(self) -> Path:
        if self.work_dir is None:
            raise ValueError(f"worker {self.config.name} has no work directory")
        return self.work_dir


class Poller(AsyncCommandMixin):
    """Singleton poller that scans ready issues and routes them."""

    def __init__(self, settings: Settings, workers: list[Worker]) -> None:
        self.settings = settings
        self.workers = workers
        self._repo_counters: dict[str, int] = {}
        self._poll_count = 0

    async def poll_loop(self) -> None:
        """Poll chainlink and dispatch issues to worker queues."""
        self.log("poller_start", poll_seconds=self.settings.poll_interval_seconds)
        try:
            await self.reap_stale_claims()
            while True:
                try:
                    await self.dispatch_ready_issues()
                    self._poll_count += 1
                    if self._poll_count % REAP_EVERY_N_POLLS == 0:
                        await self.reap_stale_claims()
                except Exception as exc:
                    self.log("poller_error", error=str(exc))
                await asyncio.sleep(self.settings.poll_interval_seconds)
        except asyncio.CancelledError:
            self.log("poller_stop")
            raise

    async def dispatch_ready_issues(self) -> None:
        """Fetch all ready issues and enqueue them onto workers."""
        for issue in await self.list_ready_issues():
            issue_id = int(issue["id"])
            if self._issue_already_assigned(issue_id):
                continue

            worker = self.route_issue(issue)
            if worker is None:
                self.log("issue_unrouted", issue_id=issue_id, title=issue.get("title") or "")
                continue

            queued = await worker.enqueue_issue(issue)
            if queued:
                self.log(
                    "issue_dispatched",
                    issue_id=issue_id,
                    worker=worker.config.name,
                    queue_size=worker.queue.qsize(),
                )

    async def list_ready_issues(self) -> list[dict[str, Any]]:
        """Fetch ready issues, preferring `issue ready --json`."""
        result = await self._run_chainlink("issue", "ready", "--json")
        data = self._try_parse_json(result.stdout)

        issue_ids: list[int] = []
        if isinstance(data, list):
            issue_ids = [
                issue_id
                for issue_id in (self._coerce_issue_id(item) for item in data)
                if issue_id is not None
            ]
        elif result.stdout.strip():
            issue_ids = self._parse_ready_issue_ids(result.stdout)

        if issue_ids:
            issues: list[dict[str, Any]] = []
            for issue_id in issue_ids:
                issues.append(await self.get_issue(issue_id))
            issues.sort(key=self._issue_sort_key)
            return issues

        issues = await self.list_open_issues()
        return [issue for issue in issues if self.is_issue_ready(issue)]

    async def list_open_issues(self) -> list[dict[str, Any]]:
        """List open issues, sorted for deterministic fallback polling."""
        data = await self._run_chainlink_json("issue", "list", "--json", "-s", "open")
        if not isinstance(data, list):
            return []

        issues = [item for item in data if isinstance(item, dict) and item.get("id") is not None]
        issues.sort(key=self._issue_sort_key)
        return issues

    async def list_in_progress_issues(self) -> list[dict[str, Any]]:
        """List issues currently marked in progress."""
        data = await self._run_chainlink_json("issue", "list", "--json", "-l", "in-progress")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("id") is not None]

    async def get_issue(self, issue_id: int) -> dict[str, Any]:
        """Fetch a full issue payload."""
        data = await self._run_chainlink_json("show", str(issue_id), "--json")
        if not isinstance(data, dict):
            raise ValueError(f"expected issue dict for #{issue_id}, got {type(data).__name__}")
        return data

    def route_issue(self, issue: dict[str, Any]) -> Worker | None:
        """Route an issue by explicit repo label, then by text match."""
        label_candidates = self._match_workers_from_labels(issue)
        if label_candidates:
            return self._choose_worker(label_candidates)

        text_candidates = self._match_workers_from_text(issue)
        if text_candidates:
            return self._choose_worker(text_candidates)
        return None

    async def reap_stale_claims(self) -> None:
        """Reclaim startup leftovers and request aborts for stale owned work."""
        for issue in await self.list_in_progress_issues():
            issue_id = int(issue["id"])
            owner = self._owner_for_issue(issue_id)
            if owner is None:
                await self._run_chainlink("unlabel", str(issue_id), "in-progress", check=False)
                self.log("stale_claim_reclaimed", issue_id=issue_id, reason="unowned")
                continue

            if await owner.is_claim_stale(issue):
                owner.request_abort("stale_claim")
                self.log(
                    "stale_claim_requested",
                    issue_id=issue_id,
                    worker=owner.config.name,
                )

    def is_issue_ready(self, issue: dict[str, Any]) -> bool:
        """Apply the worker readiness filters."""
        status = str(issue.get("status") or "").strip().lower()
        if status != "open":
            return False

        labels = {
            str(label).strip().lower()
            for label in issue.get("labels") or []
            if str(label).strip()
        }
        if labels & READY_EXCLUDED_LABELS:
            return False

        if issue.get("blocked_by"):
            return False

        assignee = self._extract_assignee(issue)
        if assignee and assignee != self.settings.agent_id:
            return False

        return True

    def _match_workers_from_labels(self, issue: dict[str, Any]) -> list[Worker]:
        labels = [str(label).strip() for label in issue.get("labels") or [] if str(label).strip()]
        repo_keys = [label.split(":", 1)[1].strip() for label in labels if label.startswith("repo:")]
        if not repo_keys:
            return []

        matched_groups: list[list[Worker]] = []
        for key in repo_keys:
            direct_matches = [worker for worker in self.workers if worker.config.name == key]
            if not direct_matches:
                continue
            matched_groups.append(self._workers_for_repo(direct_matches[0].config.repo))

        if not matched_groups:
            return []
        return self._collapse_groups(matched_groups)

    def _match_workers_from_text(self, issue: dict[str, Any]) -> list[Worker]:
        corpus_parts = [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
        ]
        milestone = issue.get("milestone")
        if isinstance(milestone, dict):
            corpus_parts.append(str(milestone.get("name") or ""))
            corpus_parts.append(str(milestone.get("description") or ""))
        corpus = "\n".join(corpus_parts).lower()

        matched_groups: list[list[Worker]] = []
        for worker in self.workers:
            if worker.config.name.lower() not in corpus:
                continue
            matched_groups.append(self._workers_for_repo(worker.config.repo))

        if not matched_groups:
            return []
        return self._collapse_groups(matched_groups)

    def _collapse_groups(self, groups: list[list[Worker]]) -> list[Worker]:
        repo_keys = {self._repo_key(group[0].config.repo) for group in groups if group}
        if len(repo_keys) > 1:
            return []
        if not groups:
            return []
        return groups[0]

    def _choose_worker(self, candidates: list[Worker]) -> Worker | None:
        if not candidates:
            return None

        repo_key = self._repo_key(candidates[0].config.repo)
        start = self._repo_counters.get(repo_key, 0) % len(candidates)
        ordered = candidates[start:] + candidates[:start]
        min_load = min(worker.current_load() for worker in ordered)
        chosen = next(worker for worker in ordered if worker.current_load() == min_load)
        self._repo_counters[repo_key] = (candidates.index(chosen) + 1) % len(candidates)
        return chosen

    def _workers_for_repo(self, repo: Path) -> list[Worker]:
        key = self._repo_key(repo)
        return [worker for worker in self.workers if self._repo_key(worker.config.repo) == key]

    def _owner_for_issue(self, issue_id: int) -> Worker | None:
        for worker in self.workers:
            if worker.state.issue_id == issue_id:
                return worker
        return None

    def _issue_already_assigned(self, issue_id: int) -> bool:
        return any(worker.owns_issue(issue_id) for worker in self.workers)

    def _repo_key(self, repo: Path) -> str:
        return str(repo.expanduser().resolve())

    def _issue_sort_key(self, issue: dict[str, Any]) -> tuple[int, str, int]:
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        priority = priority_rank.get(str(issue.get("priority") or "").lower(), 3)
        created_at = str(issue.get("created_at") or "")
        issue_id = int(issue.get("id") or 0)
        return (priority, created_at, issue_id)


async def async_main(config: AppConfig, heartbeat_fd: int | None = None) -> int:
    """Create workers, start the poller, and handle shutdown."""
    workers = [Worker(worker_config, config.settings) for worker_config in config.workers]
    started_workers: list[Worker] = []
    tasks: list[asyncio.Task[Any]] = []
    loop = asyncio.get_running_loop()
    reader_registered = False

    def cancel_tasks() -> None:
        for task in tasks:
            task.cancel()

    try:
        for worker in workers:
            await worker.setup_worktree()
            started_workers.append(worker)

        poller = Poller(config.settings, workers)
        tasks = [asyncio.create_task(worker.run(), name=f"worker-{worker.config.name}") for worker in workers]
        tasks.append(asyncio.create_task(poller.poll_loop(), name="chainlink-poller"))

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, cancel_tasks)

        if heartbeat_fd is not None:
            def _handle_heartbeat() -> None:
                if os.read(heartbeat_fd, 1) == b"":
                    cancel_tasks()

            loop.add_reader(heartbeat_fd, _handle_heartbeat)
            reader_registered = True

        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if heartbeat_fd is not None and reader_registered:
            loop.remove_reader(heartbeat_fd)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        for worker in started_workers:
            await worker.teardown_worktree()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Run the chainlink backlog worker")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.toml")
    parser.add_argument(
        "--heartbeat-fd",
        type=int,
        default=None,
        help="Supervisor heartbeat pipe fd",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    config = load_config(args.config)
    return asyncio.run(async_main(config, heartbeat_fd=args.heartbeat_fd))


if __name__ == "__main__":
    raise SystemExit(main())
