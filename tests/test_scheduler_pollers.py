"""Tests for pollers.json discovery and execution in the scheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from open_strix import scheduler as scheduler_module
from open_strix.scheduler import PollerConfig, SchedulerMixin


class FakeLayout:
    """Minimal layout stub for testing."""

    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def scheduler_file(self) -> Path:
        return self.home / "scheduler.yaml"


class FakeApp(SchedulerMixin):
    """Minimal app stub that satisfies SchedulerMixin's protocol."""

    def __init__(self, home: Path) -> None:
        self.layout = FakeLayout(home)
        self.events: list[dict] = []
        self.enqueued: list = []

    def log_event(self, event_type: str, **payload) -> None:
        self.events.append({"type": event_type, **payload})

    async def enqueue_event(self, event) -> None:
        self.enqueued.append(event)


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """Create a temporary home directory with skills dir."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return tmp_path


class TestDiscoverPollers:
    def test_no_skills_dir(self, tmp_path: Path) -> None:
        app = FakeApp(tmp_path)
        # skills dir doesn't exist
        assert app._discover_pollers() == []

    def test_empty_skills_dir(self, tmp_home: Path) -> None:
        app = FakeApp(tmp_home)
        assert app._discover_pollers() == []

    def test_valid_pollers_json(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "bluesky"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {
                "name": "bluesky-mentions",
                "command": "python poller.py",
                "cron": "*/5 * * * *",
                "env": {"BLUESKY_HANDLE": "test.bsky.social"},
            }
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].name == "bluesky-mentions"
        assert pollers[0].command == "python poller.py"
        assert pollers[0].cron == "*/5 * * * *"
        assert pollers[0].env == {"BLUESKY_HANDLE": "test.bsky.social"}
        assert pollers[0].skill_dir == skill_dir

    def test_multiple_pollers_in_one_file(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "monitoring"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "check-a", "command": "python a.py", "cron": "*/10 * * * *"},
            {"name": "check-b", "command": "python b.py", "cron": "0 * * * *"},
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 2
        assert pollers[0].name == "check-a"
        assert pollers[1].name == "check-b"

    def test_multiple_skills_with_pollers(self, tmp_home: Path) -> None:
        for name in ["alpha", "beta"]:
            skill_dir = tmp_home / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
                {"name": f"{name}-poller", "command": f"python {name}.py", "cron": "*/5 * * * *"}
            ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 2
        names = {p.name for p in pollers}
        assert names == {"alpha-poller", "beta-poller"}

    def test_invalid_json(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "broken"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text("not json {{{")

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []
        assert any(e["type"] == "poller_invalid_json" for e in app.events)

    def test_bare_array_rejected(self, tmp_home: Path) -> None:
        """Top-level must be a dict, not an array."""
        skill_dir = tmp_home / "skills" / "bad"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps([{"name": "x"}]))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []
        assert any(e["type"] == "poller_invalid_format" for e in app.events)

    def test_dict_without_pollers_key(self, tmp_home: Path) -> None:
        """Dict without 'pollers' key yields no pollers (empty list from .get)."""
        skill_dir = tmp_home / "skills" / "nokey"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"name": "x"}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers == []

    def test_missing_required_fields(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "missing-command"},
            {"command": "python x.py", "cron": "* * * * *"},  # missing name
            {"name": "ok", "command": "python ok.py", "cron": "*/5 * * * *"},
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].name == "ok"

    def test_no_env_defaults_empty(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "minimal"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "simple", "command": "echo hi", "cron": "*/5 * * * *"}
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].env == {}


class TestOnPollerFire:
    @pytest.mark.asyncio
    async def test_successful_poller_with_output(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "test"
        skill_dir.mkdir(parents=True)

        # Write a script that outputs valid JSONL
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "something happened"}))\n'
        )

        poller = PollerConfig(
            name="test-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        event = app.enqueued[0]
        assert event.event_type == "poller"
        assert event.prompt == "something happened"
        assert event.scheduler_name == "test-poller"

    @pytest.mark.asyncio
    async def test_poller_no_output(self, tmp_home: Path) -> None:
        """Empty stdout still emits a poller_complete with events_emitted=0.

        Regression guard for tony-2zb: a silent successful poller run
        must not be indistinguishable from a poller that never fired.
        Every clean exit gets exactly one poller_complete event.
        """
        skill_dir = tmp_home / "skills" / "quiet"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text("pass\n")

        poller = PollerConfig(
            name="quiet-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 0
        complete = [e for e in app.events if e["type"] == "poller_complete"]
        assert len(complete) == 1, (
            f"expected exactly one poller_complete, got {app.events}"
        )
        assert complete[0]["name"] == "quiet-poller"
        assert complete[0]["events_emitted"] == 0
        # run_id is recorded for traceability.
        assert "run_id" in complete[0]

    @pytest.mark.asyncio
    async def test_poller_nonzero_exit(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "failing"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text("import sys; sys.exit(1)\n")

        poller = PollerConfig(
            name="fail-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 0
        assert any(e["type"] == "poller_nonzero_exit" for e in app.events)

    @pytest.mark.asyncio
    async def test_poller_env_vars(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "env-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json, os\n'
            'print(json.dumps({"poller": "env-test", "prompt": os.environ.get("MY_VAR", "missing")}))\n'
        )

        poller = PollerConfig(
            name="env-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={"MY_VAR": "hello"},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].prompt == "hello"

    @pytest.mark.asyncio
    async def test_poller_state_dir_and_poller_name_env(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "state-test"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json, os\n'
            'sd = os.environ.get("STATE_DIR", "")\n'
            'pn = os.environ.get("POLLER_NAME", "")\n'
            'print(json.dumps({"poller": pn, "prompt": f"dir={sd} name={pn}"}))\n'
        )

        poller = PollerConfig(
            name="state-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert f"dir={skill_dir}" in app.enqueued[0].prompt
        assert "name=state-poller" in app.enqueued[0].prompt

    @pytest.mark.asyncio
    async def test_poller_multiple_lines(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "multi"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'for i in range(3):\n'
            '    print(json.dumps({"poller": "multi", "prompt": f"event {i}"}))\n'
        )

        poller = PollerConfig(
            name="multi-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 3
        assert app.enqueued[0].prompt == "event 0"
        assert app.enqueued[1].prompt == "event 1"
        assert app.enqueued[2].prompt == "event 2"

    @pytest.mark.asyncio
    async def test_poller_invalid_json_line_skipped(self, tmp_home: Path) -> None:
        skill_dir = tmp_home / "skills" / "mixed"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print("not json")\n'
            'print(json.dumps({"poller": "mixed", "prompt": "valid line"}))\n'
        )

        poller = PollerConfig(
            name="mixed-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].prompt == "valid line"
        assert any(e["type"] == "poller_invalid_line" for e in app.events)

    @pytest.mark.asyncio
    async def test_poller_source_platform_passthrough(self, tmp_home: Path) -> None:
        """source_platform from poller JSONL flows through to AgentEvent."""
        skill_dir = tmp_home / "skills" / "platform"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "bsky", "source_platform": "bluesky", "prompt": "new reply"}))\n'
        )

        poller = PollerConfig(
            name="platform-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].source_platform == "bluesky"
        assert app.enqueued[0].prompt == "new reply"

    @pytest.mark.asyncio
    async def test_poller_no_source_platform_defaults_none(self, tmp_home: Path) -> None:
        """Missing source_platform in JSONL results in None on AgentEvent."""
        skill_dir = tmp_home / "skills" / "noplatform"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "event without platform"}))\n'
        )

        poller = PollerConfig(
            name="noplatform-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].source_platform is None

    @pytest.mark.asyncio
    async def test_poller_channel_id_and_type_passthrough(self, tmp_home: Path) -> None:
        """channel_id and channel_type from poller JSONL flow through to AgentEvent."""
        skill_dir = tmp_home / "skills" / "matrix"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "matrix", "prompt": "hello", '
            '"channel_id": "!room:matrix.org", "channel_type": "matrix"}))\n'
        )

        poller = PollerConfig(
            name="matrix-poller",
            command="python poller.py",
            cron="* * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].channel_id == "!room:matrix.org"
        assert app.enqueued[0].channel_type == "matrix"
        assert app.enqueued[0].prompt == "hello"

    @pytest.mark.asyncio
    async def test_poller_no_channel_fields_defaults_none(self, tmp_home: Path) -> None:
        """Missing channel_id/channel_type in JSONL results in None on AgentEvent."""
        skill_dir = tmp_home / "skills" / "bare"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "no channel info"}))\n'
        )

        poller = PollerConfig(
            name="bare-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].channel_id is None
        assert app.enqueued[0].channel_type is None

    @pytest.mark.asyncio
    async def test_poller_channel_id_without_type(self, tmp_home: Path) -> None:
        """channel_id and channel_type are extracted independently."""
        skill_dir = tmp_home / "skills" / "partial"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "hi", '
            '"channel_id": "12345"}))\n'
        )

        poller = PollerConfig(
            name="partial-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].channel_id == "12345"
        assert app.enqueued[0].channel_type is None

    @pytest.mark.asyncio
    async def test_poller_whitespace_channel_fields_coerced_to_none(self, tmp_home: Path) -> None:
        """Whitespace-only channel_id/channel_type are coerced to None."""
        skill_dir = tmp_home / "skills" / "ws"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "test", "prompt": "hi", '
            '"channel_id": "  ", "channel_type": " "}))\n'
        )

        poller = PollerConfig(
            name="ws-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].channel_id is None
        assert app.enqueued[0].channel_type is None


class TestPollerFireWallClockTimeout:
    """tony-2zb: a wedged await in _run_poller_fire must not hold the
    APScheduler max_instances=1 slot past POLLER_FIRE_TIMEOUT_SECONDS."""

    @pytest.mark.asyncio
    async def test_wedged_enqueue_event_times_out_and_logs(
        self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Shrink the deadline so tests stay fast; the real constant
        # (90s in production) is indirection-tested — what matters is
        # that the wrapper enforces *some* wall-clock bound.
        monkeypatch.setattr(scheduler_module, "POLLER_FIRE_TIMEOUT_SECONDS", 0.3)

        skill_dir = tmp_home / "skills" / "wedged"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "w", "prompt": "p"}))\n'
        )

        poller = PollerConfig(
            name="wedged-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)

        # Replace enqueue_event with one that never completes — simulates
        # the wedge observed on tony-vm (OIE-Mn main/eddy pollers).
        async def hang_forever(event):  # noqa: ARG001
            await asyncio.sleep(1000)

        app.enqueue_event = hang_forever  # type: ignore[assignment]

        # Must return (not hang) within the shortened deadline.
        await asyncio.wait_for(app._on_poller_fire(poller), timeout=5.0)

        timeouts = [e for e in app.events if e["type"] == "poller_fire_timeout"]
        assert len(timeouts) == 1
        assert timeouts[0]["name"] == "wedged-poller"
        assert timeouts[0]["timeout_seconds"] == 0.3

        # Because the fire was cancelled mid-enqueue, poller_complete
        # must NOT have fired — its absence + the timeout event is the
        # operator's distinguishable signal.
        assert not any(e["type"] == "poller_complete" for e in app.events)

    @pytest.mark.asyncio
    async def test_normal_poller_completes_without_timeout(
        self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fast pollers aren't penalized by the timeout wrapper."""
        monkeypatch.setattr(scheduler_module, "POLLER_FIRE_TIMEOUT_SECONDS", 5.0)

        skill_dir = tmp_home / "skills" / "fast"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "f", "prompt": "ok"}))\n'
        )

        poller = PollerConfig(
            name="fast-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert not any(e["type"] == "poller_fire_timeout" for e in app.events)
        assert any(e["type"] == "poller_complete" for e in app.events)


class TestPollerDedupeKeyRunId:
    """tony-2zb: dedupe_key must be unique across fires of the same poller.

    event_count resets each fire; pending_scheduler_keys.discard only runs
    after _event_worker completes. A wedged worker would leave earlier
    keys in the set, silently deduping future fires without a per-fire nonce.
    """

    @pytest.mark.asyncio
    async def test_dedupe_key_includes_run_id_and_event_count(
        self, tmp_home: Path
    ) -> None:
        skill_dir = tmp_home / "skills" / "dedupe"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'for i in range(2):\n'
            '    print(json.dumps({"poller": "d", "prompt": f"e{i}"}))\n'
        )

        poller = PollerConfig(
            name="dedupe-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 2
        key0 = app.enqueued[0].dedupe_key
        key1 = app.enqueued[1].dedupe_key
        assert key0.startswith("poller:dedupe-poller:")
        assert key1.startswith("poller:dedupe-poller:")
        # Same run_id, different event_count
        prefix0, ec0 = key0.rsplit(":", 1)
        prefix1, ec1 = key1.rsplit(":", 1)
        assert prefix0 == prefix1, "same fire ⇒ same run_id prefix"
        assert ec0 == "0"
        assert ec1 == "1"

    @pytest.mark.asyncio
    async def test_dedupe_key_run_id_differs_across_fires(
        self, tmp_home: Path
    ) -> None:
        """Two fires of the same poller produce disjoint dedupe_key sets."""
        skill_dir = tmp_home / "skills" / "two-fire"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "t", "prompt": "x"}))\n'
        )

        poller = PollerConfig(
            name="two-fire-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 2
        assert app.enqueued[0].dedupe_key != app.enqueued[1].dedupe_key

    @pytest.mark.asyncio
    async def test_poller_complete_records_run_id(self, tmp_home: Path) -> None:
        """run_id in poller_complete lets operators correlate events ⇄ fire."""
        skill_dir = tmp_home / "skills" / "runid"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "r", "prompt": "one"}))\n'
        )

        poller = PollerConfig(
            name="runid-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        complete = [e for e in app.events if e["type"] == "poller_complete"]
        assert len(complete) == 1
        complete_run_id = complete[0]["run_id"]
        event_key = app.enqueued[0].dedupe_key
        # The run_id in the log matches the one embedded in dedupe_key.
        assert f":{complete_run_id}:" in event_key


class TestPollersJsonRoutingDefaults:
    """tony-2zb gap D: channel_id/channel_type at pollers.json entry level
    act as per-poller defaults, applied when per-event JSON omits them."""

    def test_discover_parses_entry_level_channel_defaults(
        self, tmp_home: Path
    ) -> None:
        skill_dir = tmp_home / "skills" / "routed"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {
                "name": "matrix-monitor",
                "command": "python poller.py",
                "cron": "*/5 * * * *",
                "channel_id": "!room:matrix.org",
                "channel_type": "matrix",
            }
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert len(pollers) == 1
        assert pollers[0].channel_id == "!room:matrix.org"
        assert pollers[0].channel_type == "matrix"

    def test_discover_defaults_channel_fields_to_none(
        self, tmp_home: Path
    ) -> None:
        """Backwards compat: pollers without channel_id/channel_type are None."""
        skill_dir = tmp_home / "skills" / "unrouted"
        skill_dir.mkdir(parents=True)
        (skill_dir / "pollers.json").write_text(json.dumps({"pollers": [
            {"name": "x", "command": "python x.py", "cron": "*/5 * * * *"}
        ]}))

        app = FakeApp(tmp_home)
        pollers = app._discover_pollers()
        assert pollers[0].channel_id is None
        assert pollers[0].channel_type is None

    @pytest.mark.asyncio
    async def test_entry_level_default_applied_when_event_omits(
        self, tmp_home: Path
    ) -> None:
        skill_dir = tmp_home / "skills" / "use-default"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "u", "prompt": "no channel in event"}))\n'
        )

        poller = PollerConfig(
            name="use-default-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
            channel_id="!default:matrix.org",
            channel_type="matrix",
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        assert app.enqueued[0].channel_id == "!default:matrix.org"
        assert app.enqueued[0].channel_type == "matrix"

    @pytest.mark.asyncio
    async def test_per_event_channel_overrides_entry_level_default(
        self, tmp_home: Path
    ) -> None:
        skill_dir = tmp_home / "skills" / "override"
        skill_dir.mkdir(parents=True)
        (skill_dir / "poller.py").write_text(
            'import json\n'
            'print(json.dumps({"poller": "o", "prompt": "event wins", '
            '"channel_id": "!event:matrix.org", "channel_type": "matrix"}))\n'
        )

        poller = PollerConfig(
            name="override-poller",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
            channel_id="!default:matrix.org",
            channel_type="matrix",
        )

        app = FakeApp(tmp_home)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        # Per-event value wins so conversational pollers can still route
        # dynamically even when entry-level defaults are configured.
        assert app.enqueued[0].channel_id == "!event:matrix.org"
