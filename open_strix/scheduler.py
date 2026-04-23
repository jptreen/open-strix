from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import yaml
from apscheduler.triggers.cron import CronTrigger

from .models import AgentEvent

UTC = timezone.utc


@dataclass
class SchedulerJob:
    name: str
    prompt: str
    cron: str | None = None
    time_of_day: str | None = None
    channel_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "prompt": self.prompt}
        if self.cron:
            data["cron"] = self.cron
        if self.time_of_day:
            data["time_of_day"] = self.time_of_day
        if self.channel_id:
            data["channel_id"] = self.channel_id
        return data


@dataclass
class PollerConfig:
    """A poller declared in a skill's pollers.json."""

    name: str
    command: str
    cron: str
    env: dict[str, str]
    skill_dir: Path
    # Per-poller routing defaults. Applied when a per-event JSON line from
    # the poller's stdout does NOT itself set channel_id / channel_type.
    # See tony-2zb for context: these keys were silently ignored previously.
    channel_id: str | None = None
    channel_type: str | None = None


_SCHEDULER_LOCK = threading.RLock()

# Hard wall-clock timeout for a single _on_poller_fire invocation.
# Generous upper bound over the 60s subprocess timeout — any await path
# beyond that (enqueue_event, dedupe bookkeeping) must release within this
# window so APScheduler's max_instances=1 slot can never wedge permanently.
POLLER_FIRE_TIMEOUT_SECONDS = 90


class SchedulerMixin:
    def _load_scheduler_jobs(self) -> list[SchedulerJob]:
        if not self.layout.scheduler_file.exists():
            return []
        loaded = yaml.safe_load(self.layout.scheduler_file.read_text(encoding="utf-8"))
        if loaded is None:
            return []
        if isinstance(loaded, list):
            raw_jobs = loaded
        else:
            raw_jobs = loaded.get("jobs", [])
        jobs: list[SchedulerJob] = []
        for raw in raw_jobs:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "")).strip()
            prompt = str(raw.get("prompt", "")).strip()
            if not name or not prompt:
                continue
            cron = str(raw.get("cron", "")).strip() or None
            time_of_day = str(raw.get("time_of_day", "")).strip() or None
            channel_id = str(raw.get("channel_id", "")).strip() or None
            jobs.append(
                SchedulerJob(
                    name=name,
                    prompt=prompt,
                    cron=cron,
                    time_of_day=time_of_day,
                    channel_id=channel_id,
                ),
            )
        return jobs

    def _save_scheduler_jobs(self, jobs: list[SchedulerJob]) -> None:
        data = {"jobs": [job.to_dict() for job in jobs]}
        content = yaml.safe_dump(data, sort_keys=False)
        target = self.layout.scheduler_file
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=".scheduler.", suffix=".tmp"
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            try:
                os.chmod(tmp, os.stat(str(target)).st_mode & 0o777)
            except (OSError, FileNotFoundError):
                os.chmod(tmp, 0o644)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _discover_pollers(self) -> list[PollerConfig]:
        """Scan skill directories for pollers.json files."""
        pollers: list[PollerConfig] = []
        skills_dir = self.layout.skills_dir
        if not skills_dir.exists():
            return pollers

        for pollers_file in sorted(skills_dir.rglob("pollers.json")):
            skill_dir = pollers_file.parent
            try:
                raw = json.loads(pollers_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                self.log_event(
                    "poller_invalid_json",
                    path=str(pollers_file),
                    error=str(exc),
                )
                continue

            if isinstance(raw, dict):
                entries = raw.get("pollers", [])
                if not isinstance(entries, list):
                    self.log_event(
                        "poller_invalid_format",
                        path=str(pollers_file),
                        error="'pollers' key must be an array",
                    )
                    continue
            else:
                self.log_event(
                    "poller_invalid_format",
                    path=str(pollers_file),
                    error="expected a JSON object with 'pollers' key",
                )
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).strip()
                command = str(entry.get("command", "")).strip()
                cron = str(entry.get("cron", "")).strip()
                if not name or not command or not cron:
                    self.log_event(
                        "poller_missing_fields",
                        path=str(pollers_file),
                        entry=entry,
                    )
                    continue
                env = entry.get("env", {})
                if not isinstance(env, dict):
                    env = {}
                raw_channel_id = entry.get("channel_id")
                channel_id = (
                    str(raw_channel_id).strip() or None
                    if raw_channel_id is not None
                    else None
                )
                raw_channel_type = entry.get("channel_type")
                channel_type = (
                    str(raw_channel_type).strip() or None
                    if raw_channel_type is not None
                    else None
                )
                pollers.append(
                    PollerConfig(
                        name=name,
                        command=command,
                        cron=cron,
                        env={str(k): str(v) for k, v in env.items()},
                        skill_dir=skill_dir,
                        channel_id=channel_id,
                        channel_type=channel_type,
                    ),
                )
        return pollers

    def _reload_scheduler_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("open_strix:"):
                self.scheduler.remove_job(job.id)

        # Register prompt-based scheduler jobs from scheduler.yaml.
        for job in self._load_scheduler_jobs():
            if bool(job.cron) == bool(job.time_of_day):
                self.log_event("scheduler_invalid_job", name=job.name)
                continue

            trigger: CronTrigger
            if job.cron:
                try:
                    trigger = CronTrigger.from_crontab(job.cron, timezone=UTC)
                except ValueError as exc:
                    self.log_event("scheduler_invalid_cron", name=job.name, error=str(exc))
                    continue
            else:
                try:
                    hour_str, minute_str = str(job.time_of_day).split(":")
                    trigger = CronTrigger(
                        hour=int(hour_str),
                        minute=int(minute_str),
                        timezone=UTC,
                    )
                except (TypeError, ValueError) as exc:
                    self.log_event("scheduler_invalid_time", name=job.name, error=str(exc))
                    continue

            self.scheduler.add_job(
                self._on_scheduler_fire,
                trigger=trigger,
                kwargs={
                    "name": job.name,
                    "prompt": job.prompt,
                    "channel_id": job.channel_id,
                },
                id=f"open_strix:{job.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        # Register pollers from skills/*/pollers.json.
        pollers = self._discover_pollers()
        for poller in pollers:
            try:
                trigger = CronTrigger.from_crontab(poller.cron, timezone=UTC)
            except ValueError as exc:
                self.log_event(
                    "poller_invalid_cron",
                    name=poller.name,
                    cron=poller.cron,
                    error=str(exc),
                )
                continue

            self.scheduler.add_job(
                self._on_poller_fire,
                trigger=trigger,
                kwargs={"poller": poller},
                id=f"open_strix:poller:{poller.name}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )

        scheduler_count = len(self._load_scheduler_jobs())
        self.log_event(
            "scheduler_reloaded",
            jobs=scheduler_count,
            pollers=len(pollers),
        )

    async def _on_scheduler_fire(self, name: str, prompt: str, channel_id: str | None = None) -> None:
        # Async callback keeps scheduler execution on the event loop.
        await self.enqueue_event(
            AgentEvent(
                event_type="scheduler",
                prompt=prompt,
                channel_id=channel_id,
                scheduler_name=name,
                dedupe_key=f"scheduler:{name}",
            ),
        )

    async def _on_poller_fire(self, poller: PollerConfig) -> None:
        """Run a poller and release the APScheduler slot within a hard deadline.

        Wraps :meth:`_run_poller_fire` in a wall-clock timeout so that a
        wedged ``await`` (e.g. a stuck enqueue_event on a backed-up queue)
        cannot permanently hold the ``max_instances=1`` slot for this
        poller. See tony-2zb / tony-761 for the forensic context.
        """
        try:
            await asyncio.wait_for(
                self._run_poller_fire(poller),
                timeout=POLLER_FIRE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.log_event(
                "poller_fire_timeout",
                name=poller.name,
                timeout_seconds=POLLER_FIRE_TIMEOUT_SECONDS,
            )

    async def _run_poller_fire(self, poller: PollerConfig) -> None:
        """Run a poller subprocess and enqueue events from its stdout.

        Always emits exactly one ``poller_complete`` event per invocation
        when the subprocess exits cleanly (even with empty stdout), so
        the absence of a log is unambiguous signal that a fire never
        completed (timeout, exec error, or non-zero exit).
        """
        env = {**os.environ, **poller.env}
        env["STATE_DIR"] = str(poller.skill_dir)
        env["POLLER_NAME"] = poller.name

        # Per-fire nonce so dedupe_key values can never collide across
        # separate fires of the same poller. event_count resets to 0
        # each fire, and pending_scheduler_keys.discard only runs after
        # _event_worker finishes processing — if the worker is wedged,
        # the previous fire's poller:<name>:0 key remains, silently
        # deduping the next fire's first event without this nonce.
        run_id = uuid.uuid4().hex[:8]

        try:
            proc = await asyncio.create_subprocess_shell(
                poller.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(poller.skill_dir),
                env=env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=60,
            )
        except asyncio.TimeoutError:
            self.log_event(
                "poller_timeout",
                name=poller.name,
                timeout_seconds=60,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return
        except Exception as exc:
            self.log_event(
                "poller_exec_error",
                name=poller.name,
                error=str(exc),
            )
            return

        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        if stderr_text:
            self.log_event(
                "poller_stderr",
                name=poller.name,
                stderr=stderr_text[:2000],
            )

        if proc.returncode != 0:
            self.log_event(
                "poller_nonzero_exit",
                name=poller.name,
                returncode=proc.returncode,
            )
            return

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()

        event_count = 0
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                self.log_event(
                    "poller_invalid_line",
                    name=poller.name,
                    line=line[:500],
                )
                continue

            if not isinstance(parsed, dict):
                continue

            # Backfill batch: poller seeds prior channel history into
            # message_history_all so the agent has context for a conversation
            # it was offline for, without firing a turn on each historical
            # message. Discord's _refresh_channel_history_from_discord does
            # the same thing inside the discord.py client path; this is the
            # poller-side equivalent. Shape:
            #   {"type": "history_backfill",
            #    "channel_id": "...",
            #    "channel_type": "...",        # optional — falls back to poller default
            #    "records": [
            #       {"sender": ..., "content": ...,
            #        "event_id": ..., "timestamp": ..., "is_bot": ...,
            #        "attachment_names": [...]}]}
            if parsed.get("type") == "history_backfill":
                bf_channel_id = parsed.get("channel_id")
                if bf_channel_id is not None:
                    bf_channel_id = str(bf_channel_id).strip() or None
                if bf_channel_id is None:
                    bf_channel_id = poller.channel_id
                bf_channel_type = parsed.get("channel_type")
                if bf_channel_type is not None:
                    bf_channel_type = str(bf_channel_type).strip() or None
                if bf_channel_type is None:
                    bf_channel_type = poller.channel_type
                records = parsed.get("records") or []
                if not bf_channel_id or not isinstance(records, list):
                    self.log_event(
                        "poller_backfill_invalid",
                        name=poller.name,
                        channel_id=bf_channel_id,
                        record_count=len(records) if isinstance(records, list) else 0,
                    )
                    continue
                recorded = 0
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    rec_content = str(rec.get("content") or "").strip()
                    if not rec_content:
                        continue
                    rec_message_id = str(rec.get("event_id") or rec.get("source_id") or "").strip() or None
                    rec_timestamp = str(rec.get("timestamp") or "").strip() or None
                    rec_attachments = rec.get("attachment_names") or []
                    if not isinstance(rec_attachments, list):
                        rec_attachments = []
                    added = self._remember_message(
                        channel_id=bf_channel_id,
                        author=str(rec.get("sender") or rec.get("author") or "unknown"),
                        content=rec_content,
                        attachment_names=[str(a) for a in rec_attachments],
                        message_id=rec_message_id,
                        is_bot=bool(rec.get("is_bot")),
                        source=bf_channel_type or "poller",
                        timestamp=rec_timestamp,
                    )
                    if added:
                        recorded += 1
                self.log_event(
                    "poller_history_backfill",
                    name=poller.name,
                    channel_id=bf_channel_id,
                    channel_type=bf_channel_type,
                    records_seen=len(records),
                    records_recorded=recorded,
                )
                continue

            prompt = str(parsed.get("prompt", "")).strip()
            if not prompt:
                continue

            source_platform = parsed.get("source_platform")
            if source_platform is not None:
                source_platform = str(source_platform).strip() or None

            channel_id = parsed.get("channel_id")
            if channel_id is not None:
                channel_id = str(channel_id).strip() or None
            # Fall back to pollers.json-level default when per-event JSON
            # doesn't specify. Per-event value wins if present.
            if channel_id is None:
                channel_id = poller.channel_id

            channel_type = parsed.get("channel_type")
            if channel_type is not None:
                channel_type = str(channel_type).strip() or None
            if channel_type is None:
                channel_type = poller.channel_type

            # Extract author (sender) and source_id (event_id) so
            # conversational pollers can populate message_history.
            author = parsed.get("sender") or parsed.get("author")
            if author is not None:
                author = str(author).strip() or None

            source_id = parsed.get("event_id") or parsed.get("source_id")
            if source_id is not None:
                source_id = str(source_id).strip() or None

            await self.enqueue_event(
                AgentEvent(
                    event_type="poller",
                    prompt=prompt,
                    channel_id=channel_id,
                    channel_type=channel_type,
                    scheduler_name=poller.name,
                    dedupe_key=f"poller:{poller.name}:{run_id}:{event_count}",
                    source_platform=source_platform,
                    author=author,
                    source_id=source_id,
                ),
            )
            event_count += 1

        self.log_event(
            "poller_complete",
            name=poller.name,
            events_emitted=event_count,
            run_id=run_id,
        )
