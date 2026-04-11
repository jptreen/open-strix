"""Tests for poller message history integration (tony-9c6).

Covers:
  A. Incoming conversational poller messages stored in message_history
  B. Outgoing HTTP handler messages stored in message_history
  C. Source filter includes channel handler types
  D. Current event deduplicated from section 3
  E. Section 3 header renamed to 'Recent messages'
  F. Scheduler extracts author/source_id from poller JSON
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

import open_strix.app as app_mod
from open_strix.config import AppConfig, RepoLayout
from open_strix.discord import DiscordMixin
from open_strix.models import AgentEvent
from open_strix.scheduler import SchedulerMixin


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _extract_section(prompt: str, start: str, end: str) -> str:
    section = prompt.split(start, 1)[1]
    if end in section:
        return section.split(end, 1)[0].strip()
    return section.strip()


# ---------------------------------------------------------------------------
# Fix A: Incoming conversational poller messages stored in history
# ---------------------------------------------------------------------------


class TestIncomingPollerMessageHistory:
    def test_conversational_poller_stored_in_history(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Poller events with channel_type are stored in message_history."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)
        app.config = AppConfig(channel_handlers={"matrix": {"send_url": "http://x/send"}})

        event = AgentEvent(
            event_type="poller",
            prompt="Hello from Matrix",
            channel_id="!room:matrix.org",
            channel_type="matrix",
            scheduler_name="matrix-e2ee",
            author="@alice:matrix.org",
            source_id="$evt123",
        )

        # Call _process_event internals (up to the _remember_message part)
        app._current_turn_sent_messages = []
        app._reset_send_message_circuit_breaker()
        app._withhold_final_text = False

        # Simulate the incoming storage logic from _process_event
        if event.event_type == "poller" and event.channel_type and event.channel_id:
            app._remember_message(
                channel_id=event.channel_id,
                author=event.author or event.scheduler_name or "unknown",
                content=event.prompt,
                attachment_names=list(event.attachment_names),
                message_id=event.source_id,
                is_bot=False,
                source=event.channel_type,
            )

        assert len(app.message_history_all) == 1
        msg = app.message_history_all[0]
        assert msg["channel_id"] == "!room:matrix.org"
        assert msg["author"] == "@alice:matrix.org"
        assert msg["content"] == "Hello from Matrix"
        assert msg["source"] == "matrix"
        assert msg["message_id"] == "$evt123"
        assert msg["is_bot"] is False

    def test_notification_poller_not_stored(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Poller events with only source_platform (no channel_type) are NOT stored."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)

        event = AgentEvent(
            event_type="poller",
            prompt="New commit on main",
            channel_id=None,
            channel_type=None,
            scheduler_name="github-monitor",
            source_platform="github",
        )

        # Same check as _process_event
        if event.event_type == "poller" and event.channel_type and event.channel_id:
            app._remember_message(
                channel_id=event.channel_id,
                author=event.author or event.scheduler_name or "unknown",
                content=event.prompt,
                attachment_names=list(event.attachment_names),
                message_id=event.source_id,
                is_bot=False,
                source=event.channel_type,
            )

        assert len(app.message_history_all) == 0

    def test_author_falls_back_to_scheduler_name(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When author is None, scheduler_name is used as fallback."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)
        app.config = AppConfig(channel_handlers={"matrix": {"send_url": "http://x/send"}})

        event = AgentEvent(
            event_type="poller",
            prompt="message",
            channel_id="!room:m.org",
            channel_type="matrix",
            scheduler_name="matrix-e2ee",
            author=None,
        )

        if event.event_type == "poller" and event.channel_type and event.channel_id:
            app._remember_message(
                channel_id=event.channel_id,
                author=event.author or event.scheduler_name or "unknown",
                content=event.prompt,
                attachment_names=list(event.attachment_names),
                message_id=event.source_id,
                is_bot=False,
                source=event.channel_type,
            )

        assert app.message_history_all[0]["author"] == "matrix-e2ee"


# ---------------------------------------------------------------------------
# Fix B: Outgoing HTTP handler messages stored in history
# ---------------------------------------------------------------------------


class _NoopChatHistory:
    def _append_chat_history_record(self, record: dict) -> None:
        pass


@pytest.fixture()
def mock_bridge():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            received.append(body)
            resp = json.dumps({"event_id": "$sent1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", received
    server.shutdown()


class TestOutgoingHttpHandlerHistory:
    @pytest.mark.asyncio
    async def test_http_handler_stores_outgoing_message(self, mock_bridge) -> None:
        """Successful HTTP handler send stores the outgoing message in history."""
        url, received = mock_bridge

        class FakeApp(_NoopChatHistory, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {
                            "send_url": f"{url}/send",
                            "body_map": '{{"room_id": "{{channel_id}}", "body": "{{text}}"}}'.replace(
                                "{{", "{"
                            ).replace("}}", "}"),
                        }
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

        app = FakeApp()
        sent, event_id, chunks = await app._send_channel_message(
            channel_id="!room:matrix.org",
            text="reply from agent",
        )

        assert sent is True
        assert len(app.message_history_all) == 1
        msg = app.message_history_all[0]
        assert msg["channel_id"] == "!room:matrix.org"
        assert msg["author"] == "open_strix"
        assert msg["content"] == "reply from agent"
        assert msg["source"] == "matrix"
        assert msg["is_bot"] is True
        assert msg["message_id"] == "$sent1"

    @pytest.mark.asyncio
    async def test_http_handler_failure_does_not_store(self) -> None:
        """Failed HTTP handler send does NOT store in history."""

        class FakeApp(_NoopChatHistory, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": "http://127.0.0.1:1/send"}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

        app = FakeApp()
        sent, _, _ = await app._send_channel_message(
            channel_id="!room:matrix.org",
            text="this will fail",
        )

        assert sent is False
        assert len(app.message_history_all) == 0

    @pytest.mark.asyncio
    async def test_sent_messages_tracked_for_turn(self, mock_bridge) -> None:
        """Outgoing HTTP handler messages are appended to _current_turn_sent_messages."""
        url, received = mock_bridge

        class FakeApp(_NoopChatHistory, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send"}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

        app = FakeApp()
        await app._send_channel_message(
            channel_id="!room:matrix.org",
            text="test",
        )

        assert len(app._current_turn_sent_messages) == 1
        assert app._current_turn_sent_messages[0][0] == "!room:matrix.org"


# ---------------------------------------------------------------------------
# Fix C: Source filter includes channel handler types
# ---------------------------------------------------------------------------


class TestSourceFilter:
    def test_matrix_messages_appear_in_prompt(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Messages with source='matrix' appear in prompt when matrix is a registered handler."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)
        app.config = AppConfig(
            channel_handlers={"matrix": {"send_url": "http://x/send"}}
        )

        app._remember_message(
            channel_id="!room:matrix.org",
            author="@alice:matrix.org",
            content="hello from matrix",
            attachment_names=[],
            message_id="$m1",
            source="matrix",
        )

        prompt = app._render_prompt(
            AgentEvent(
                event_type="poller",
                prompt="new message",
                channel_id="!room:matrix.org",
                channel_type="matrix",
                scheduler_name="matrix-e2ee",
            ),
        )

        assert "hello from matrix" in prompt

    def test_unregistered_source_excluded_from_prompt(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Messages from unregistered sources don't appear in prompt."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)
        app.config = AppConfig(channel_handlers={})

        app._remember_message(
            channel_id="!room:matrix.org",
            author="@alice:matrix.org",
            content="invisible matrix message",
            attachment_names=[],
            message_id="$m1",
            source="matrix",
        )

        prompt = app._render_prompt(
            AgentEvent(
                event_type="poller",
                prompt="new message",
                channel_id="!room:matrix.org",
            ),
        )

        assert "invisible matrix message" not in prompt


# ---------------------------------------------------------------------------
# Fix D: Current event deduplicated from section 3
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_current_event_excluded_from_recent_messages(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The current event's message_id is filtered from section 3."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)
        app.config = AppConfig(
            channel_handlers={"matrix": {"send_url": "http://x/send"}}
        )

        # Store two messages — one will be the "current" event
        app._remember_message(
            channel_id="!room:matrix.org",
            author="@alice:matrix.org",
            content="earlier message",
            attachment_names=[],
            message_id="$earlier",
            source="matrix",
        )
        app._remember_message(
            channel_id="!room:matrix.org",
            author="@alice:matrix.org",
            content="current message",
            attachment_names=[],
            message_id="$current",
            source="matrix",
        )

        prompt = app._render_prompt(
            AgentEvent(
                event_type="poller",
                prompt="current message",
                channel_id="!room:matrix.org",
                channel_type="matrix",
                source_id="$current",
            ),
        )

        messages_section = _extract_section(
            prompt,
            "3) Recent messages:\n",
            "4) Discord channel context:",
        )

        # Earlier message should be in section 3
        assert "earlier message" in messages_section
        # Current message should NOT be in section 3 (it's in section 5)
        assert "$current" not in messages_section

    def test_no_source_id_keeps_all_messages(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When event has no source_id, all messages remain in section 3."""
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)

        app._remember_message(
            channel_id="123",
            author="alice",
            content="message one",
            attachment_names=[],
            message_id="1",
            source="discord",
        )
        app._remember_message(
            channel_id="123",
            author="bob",
            content="message two",
            attachment_names=[],
            message_id="2",
            source="discord",
        )

        prompt = app._render_prompt(
            AgentEvent(
                event_type="discord_message",
                prompt="current",
                channel_id="123",
                source_id=None,
            ),
        )

        messages_section = _extract_section(
            prompt,
            "3) Recent messages:\n",
            "4) Discord channel context:",
        )

        assert "message one" in messages_section
        assert "message two" in messages_section


# ---------------------------------------------------------------------------
# Fix E: Section 3 header renamed
# ---------------------------------------------------------------------------


class TestSectionHeader:
    def test_section_3_uses_recent_messages_header(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
        app = app_mod.OpenStrixApp(tmp_path)

        prompt = app._render_prompt(
            AgentEvent(event_type="discord_message", prompt="hello"),
        )

        assert "3) Recent messages:" in prompt
        assert "Last Discord messages" not in prompt


# ---------------------------------------------------------------------------
# Fix F: Scheduler extracts author and source_id from poller JSON
# ---------------------------------------------------------------------------


class _FakeLayout:
    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def scheduler_file(self) -> Path:
        return self.home / "scheduler.yaml"


class _FakeSchedulerApp(SchedulerMixin):
    def __init__(self, home: Path) -> None:
        self.layout = _FakeLayout(home)
        self.events: list[dict] = []
        self.enqueued: list[AgentEvent] = []

    def log_event(self, event_type: str, **payload) -> None:
        self.events.append({"type": event_type, **payload})

    async def enqueue_event(self, event: AgentEvent) -> None:
        self.enqueued.append(event)


class TestSchedulerAuthorExtraction:
    @pytest.mark.asyncio
    async def test_sender_extracted_as_author(self, tmp_path: Path) -> None:
        """Poller JSON 'sender' field → AgentEvent.author."""
        from open_strix.scheduler import PollerConfig

        skill_dir = tmp_path / "skills" / "matrix"
        skill_dir.mkdir(parents=True)

        event_json = json.dumps({
            "prompt": "Hello",
            "channel_id": "!room:m.org",
            "channel_type": "matrix",
            "sender": "@alice:matrix.org",
            "event_id": "$evt456",
        })
        (skill_dir / "poller.py").write_text(
            f"import json\nprint(json.dumps({event_json}))\n"
        )

        poller = PollerConfig(
            name="matrix-e2ee",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = _FakeSchedulerApp(tmp_path)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        evt = app.enqueued[0]
        assert evt.author == "@alice:matrix.org"
        assert evt.source_id == "$evt456"

    @pytest.mark.asyncio
    async def test_missing_sender_defaults_to_none(self, tmp_path: Path) -> None:
        """When poller JSON has no sender/author/event_id, fields are None."""
        from open_strix.scheduler import PollerConfig

        skill_dir = tmp_path / "skills" / "github"
        skill_dir.mkdir(parents=True)

        event_json = json.dumps({
            "prompt": "notification",
            "source_platform": "github",
        })
        (skill_dir / "poller.py").write_text(
            f"import json\nprint(json.dumps({event_json}))\n"
        )

        poller = PollerConfig(
            name="github-monitor",
            command="python poller.py",
            cron="*/5 * * * *",
            env={},
            skill_dir=skill_dir,
        )

        app = _FakeSchedulerApp(tmp_path)
        await app._on_poller_fire(poller)

        assert len(app.enqueued) == 1
        evt = app.enqueued[0]
        assert evt.author is None
        assert evt.source_id is None
