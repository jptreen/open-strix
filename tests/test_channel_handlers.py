"""Tests for the pluggable channel handler registry."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.config import AppConfig, _parse_channel_handlers
from open_strix.models import AgentEvent


class _MessageHistoryStub:
    """Mixin providing message_history stubs for FakeApp test classes."""

    def _append_chat_history_record(self, record: dict) -> None:
        pass  # no-op in tests — skip disk persistence


class TestParseChannelHandlers:
    def test_empty_config(self) -> None:
        assert _parse_channel_handlers(None) == {}
        assert _parse_channel_handlers({}) == {}
        assert _parse_channel_handlers("not a dict") == {}

    def test_valid_handler(self) -> None:
        raw = {
            "matrix": {
                "send_url": "http://127.0.0.1:29317/send",
                "body_map": '{"room_id": "{channel_id}", "body": "{text}"}',
            }
        }
        result = _parse_channel_handlers(raw)
        assert "matrix" in result
        assert result["matrix"]["send_url"] == "http://127.0.0.1:29317/send"

    def test_multiple_handlers(self) -> None:
        raw = {
            "matrix": {"send_url": "http://localhost:29317/send"},
            "slack": {"send_url": "http://localhost:8080/send"},
        }
        result = _parse_channel_handlers(raw)
        assert len(result) == 2
        assert "matrix" in result
        assert "slack" in result

    def test_invalid_handler_entry_skipped(self) -> None:
        raw = {
            "matrix": {"send_url": "http://localhost/send"},
            "bad": "not a dict",
            "": {"send_url": "http://blank-key/send"},
        }
        result = _parse_channel_handlers(raw)
        assert len(result) == 1
        assert "matrix" in result

    def test_app_config_defaults_empty(self) -> None:
        config = AppConfig()
        assert config.channel_handlers == {}


@pytest.fixture
def mock_bridge():
    """Start a tiny HTTP server that mimics the Matrix bridge POST /send."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            received.append(body)
            response = json.dumps({"ok": True, "event_id": "$test123"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.encode())

        def log_message(self, format, *args):
            pass  # silence logs

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", received
    server.shutdown()


class TestSendViaHttpHandler:
    """Test the HTTP dispatch path in _send_channel_message."""

    @pytest.mark.asyncio
    async def test_http_handler_dispatches(self, mock_bridge) -> None:
        """A config-driven handler POSTs to the configured URL."""
        url, received = mock_bridge

        # Minimal stub that satisfies _send_channel_message's protocol.
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                body_map = '{"room_id": "{channel_id}", "body": "{text}"}'
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {
                            "send_url": f"{url}/send",
                            "body_map": body_map,
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
            text="hello from test",
        )

        assert sent is True
        assert event_id == "$test123"
        assert chunks == 1
        assert len(received) == 1
        assert received[0]["room_id"] == "!room:matrix.org"
        assert received[0]["body"] == "hello from test"

    @pytest.mark.asyncio
    async def test_explicit_channel_type_overrides_current(self, mock_bridge) -> None:
        """Explicit channel_type routes to the specified handler, not current_channel_type."""
        url, received = mock_bridge

        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                body_map = '{"room_id": "{channel_id}", "body": "{text}"}'
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {
                            "send_url": f"{url}/send",
                            "body_map": body_map,
                        }
                    }
                )
                self.current_channel_type = "discord"  # current is discord
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
            text="cross-post",
            channel_type="matrix",  # explicit override
        )

        assert sent is True
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_no_handler_falls_through_to_discord(self) -> None:
        """Unregistered channel_type falls through to Discord path."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(channel_handlers={})
                self.current_channel_type = None
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []
                self.discord_send_called = False

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

            async def _send_discord_message(self, **kwargs):
                self.discord_send_called = True
                return True, "12345", 1

        app = FakeApp()
        sent, _, _ = await app._send_channel_message(
            channel_id="123456789",
            text="normal discord message",
        )

        assert sent is True
        assert app.discord_send_called

    @pytest.mark.asyncio
    async def test_unreachable_handler_returns_failure(self) -> None:
        """HTTP handler that can't connect returns failure, doesn't crash."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
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
        sent, event_id, chunks = await app._send_channel_message(
            channel_id="!room:matrix.org",
            text="hello",
        )

        assert sent is False
        assert event_id is None
        assert chunks == 0
        assert any(e["type"] == "channel_handler_error" for e in app.events)

    @pytest.mark.asyncio
    async def test_handler_missing_send_url(self) -> None:
        """Handler config without send_url returns failure."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"body_map": "{}"}}
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
            text="hello",
        )

        assert sent is False


class TestJsonEscaping:
    """Verify body_map template properly escapes JSON-special characters."""

    @pytest.mark.asyncio
    async def test_text_with_double_quotes(self, mock_bridge) -> None:
        url, received = mock_bridge
        app = self._make_app(url)
        await app._send_channel_message(
            channel_id="!room:matrix.org", text='Hello "world"',
        )
        assert len(received) == 1
        assert received[0]["body"] == 'Hello "world"'

    @pytest.mark.asyncio
    async def test_text_with_backslashes(self, mock_bridge) -> None:
        url, received = mock_bridge
        app = self._make_app(url)
        await app._send_channel_message(
            channel_id="!room:matrix.org", text="path\\to\\file",
        )
        assert len(received) == 1
        assert received[0]["body"] == "path\\to\\file"

    @pytest.mark.asyncio
    async def test_text_with_newlines(self, mock_bridge) -> None:
        url, received = mock_bridge
        app = self._make_app(url)
        await app._send_channel_message(
            channel_id="!room:matrix.org", text="line1\nline2",
        )
        assert len(received) == 1
        assert received[0]["body"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_text_with_json_injection_attempt(self, mock_bridge) -> None:
        url, received = mock_bridge
        app = self._make_app(url)
        await app._send_channel_message(
            channel_id="!room:matrix.org",
            text='safe", "admin": true, "x": "pwned',
        )
        assert len(received) == 1
        # The injected keys should NOT appear — text is a single string value
        assert "admin" not in received[0]
        assert received[0]["body"] == 'safe", "admin": true, "x": "pwned'

    @pytest.mark.asyncio
    async def test_channel_id_with_quotes(self, mock_bridge) -> None:
        url, received = mock_bridge
        app = self._make_app(url)
        await app._send_channel_message(
            channel_id='!room"evil:matrix.org', text="hello",
        )
        assert len(received) == 1
        assert received[0]["room_id"] == '!room"evil:matrix.org'

    @pytest.mark.asyncio
    async def test_default_body_without_template(self, mock_bridge) -> None:
        """When body_map is empty, fallback uses json.dumps (always safe)."""
        url, received = mock_bridge

        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"send_url": f"{url}/send"}}
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
            channel_id="!room:matrix.org", text='quotes "here"',
        )
        assert len(received) == 1
        assert received[0]["text"] == 'quotes "here"'

    @staticmethod
    def _make_app(url):
        from open_strix.discord import DiscordMixin

        body_map = '{"room_id": "{channel_id}", "body": "{text}"}'

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
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

        return FakeApp()


class TestAttachmentWarning:
    """Verify attachments log a warning when routed to HTTP handlers."""

    @pytest.mark.asyncio
    async def test_attachments_logged_as_unsupported(self, mock_bridge) -> None:
        url, received = mock_bridge

        from open_strix.discord import DiscordMixin

        body_map = '{"room_id": "{channel_id}", "body": "{text}"}'

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
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
        from pathlib import Path
        await app._send_channel_message(
            channel_id="!room:matrix.org",
            text="hello",
            attachment_paths=[Path("/tmp/file.png")],
        )
        assert any(
            e["type"] == "channel_handler_attachments_unsupported"
            for e in app.events
        )
        # Message still sent (text portion)
        assert len(received) == 1


class TestReservedChannelTypes:
    """Verify reserved channel types cannot shadow built-in routing."""

    def test_discord_key_rejected(self) -> None:
        raw = {"discord": {"send_url": "http://evil/send"}, "matrix": {"send_url": "http://ok/send"}}
        result = _parse_channel_handlers(raw)
        assert "discord" not in result
        assert "matrix" in result

    def test_web_key_rejected(self) -> None:
        raw = {"web": {"send_url": "http://evil"}, "local-web": {"send_url": "http://evil"}}
        result = _parse_channel_handlers(raw)
        assert "web" not in result
        assert "local-web" not in result

    def test_stdin_key_rejected(self) -> None:
        raw = {"stdin": {"send_url": "http://evil"}}
        result = _parse_channel_handlers(raw)
        assert "stdin" not in result

    def test_non_reserved_accepted(self) -> None:
        raw = {"matrix": {"send_url": "http://ok"}, "slack": {"send_url": "http://ok"}}
        result = _parse_channel_handlers(raw)
        assert len(result) == 2


class TestNonBlockingIO:
    """Verify HTTP handler does not block the event loop."""

    @pytest.mark.asyncio
    async def test_event_loop_not_blocked(self, mock_bridge) -> None:
        """A concurrent task should complete while the HTTP handler runs."""
        import asyncio
        url, _ = mock_bridge

        from open_strix.discord import DiscordMixin

        body_map = '{"room_id": "{channel_id}", "body": "{text}"}'

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
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
        flag = False

        async def set_flag():
            nonlocal flag
            await asyncio.sleep(0)
            flag = True

        # Both should run concurrently; flag should be set during the HTTP call
        await asyncio.gather(
            app._send_channel_message(channel_id="!room:m.org", text="hi"),
            set_flag(),
        )
        assert flag is True


class TestEventWorkerChannelType:
    """Verify current_channel_type lifecycle in _event_worker."""

    @pytest.mark.asyncio
    async def test_agent_event_carries_channel_type(self) -> None:
        """AgentEvent dataclass correctly stores channel_type."""
        event = AgentEvent(
            event_type="poller",
            prompt="test",
            channel_id="!room:matrix.org",
            channel_type="matrix",
        )
        assert event.channel_type == "matrix"
        assert event.channel_id == "!room:matrix.org"

    @pytest.mark.asyncio
    async def test_channel_type_set_during_processing(self) -> None:
        """_event_worker sets current_channel_type before calling _process_event."""
        from open_strix.app import OpenStrixApp

        app = OpenStrixApp.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app.current_channel_id = None
        app.current_channel_type = None
        app.current_event_label = None
        app.current_turn_start = None
        app.pending_scheduler_keys = set()
        app._draining = False
        app.log_event = lambda *a, **kw: None

        captured_type = None

        async def capture_and_drain(event):
            nonlocal captured_type
            captured_type = app.current_channel_type
            # Signal drain so the worker exits after the finally block
            app._draining = True

        app._process_event = capture_and_drain

        event = AgentEvent(
            event_type="poller", prompt="test",
            channel_id="!room:matrix.org", channel_type="matrix",
        )
        app.queue.put_nowait(event)

        await app._event_worker()
        assert captured_type == "matrix"

    @pytest.mark.asyncio
    async def test_channel_type_reset_after_success(self) -> None:
        """current_channel_type is None after successful processing."""
        from open_strix.app import OpenStrixApp

        app = OpenStrixApp.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app.current_channel_id = None
        app.current_channel_type = None
        app.current_event_label = None
        app.current_turn_start = None
        app.pending_scheduler_keys = set()
        app._draining = False
        app.log_event = lambda *a, **kw: None

        async def noop_process(event):
            pass

        app._process_event = noop_process

        event = AgentEvent(
            event_type="poller", prompt="test",
            channel_id="!room:matrix.org", channel_type="matrix",
        )
        app.queue.put_nowait(event)

        # Run worker in background, let it process one event then stop
        async def stop_after_delay():
            await asyncio.sleep(0.05)
            app._draining = True
            # Push a dummy event to unblock the queue.get()
            app.queue.put_nowait(AgentEvent(event_type="dummy", prompt=""))

        await asyncio.gather(app._event_worker(), stop_after_delay())
        # After the first event (non-draining), channel_type should be reset
        assert app.current_channel_type is None

    @pytest.mark.asyncio
    async def test_channel_type_reset_after_exception(self) -> None:
        """current_channel_type is None after _process_event raises."""
        from open_strix.app import OpenStrixApp

        app = OpenStrixApp.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app.current_channel_id = None
        app.current_channel_type = None
        app.current_event_label = None
        app.current_turn_start = None
        app.pending_scheduler_keys = set()
        app._draining = False
        app.log_event = lambda *a, **kw: None

        async def exploding_process(event):
            raise RuntimeError("boom")

        app._process_event = exploding_process
        app._react_to_latest_message = AsyncMock(return_value=False)
        app.is_local_web_channel = lambda cid: False

        event = AgentEvent(
            event_type="poller", prompt="test",
            channel_id="!room:matrix.org", channel_type="matrix",
        )
        app.queue.put_nowait(event)

        async def stop_after_delay():
            await asyncio.sleep(0.05)
            app._draining = True
            app.queue.put_nowait(AgentEvent(event_type="dummy", prompt=""))

        await asyncio.gather(app._event_worker(), stop_after_delay())
        assert app.current_channel_type is None

    @pytest.mark.asyncio
    async def test_channel_type_reset_after_circuit_breaker(self) -> None:
        """current_channel_type is None after SendMessageCircuitBreakerStop."""
        from open_strix.app import OpenStrixApp
        from open_strix.tools import SendMessageCircuitBreakerStop

        app = OpenStrixApp.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app.current_channel_id = None
        app.current_channel_type = None
        app.current_event_label = None
        app.current_turn_start = None
        app.pending_scheduler_keys = set()
        app._draining = False
        app.log_event = lambda *a, **kw: None

        async def circuit_breaker_process(event):
            raise SendMessageCircuitBreakerStop("loop detected")

        app._process_event = circuit_breaker_process

        event = AgentEvent(
            event_type="poller", prompt="test",
            channel_id="!room:matrix.org", channel_type="matrix",
        )
        app.queue.put_nowait(event)

        async def stop_after_delay():
            await asyncio.sleep(0.05)
            app._draining = True
            app.queue.put_nowait(AgentEvent(event_type="dummy", prompt=""))

        await asyncio.gather(app._event_worker(), stop_after_delay())
        assert app.current_channel_type is None

    @pytest.mark.asyncio
    async def test_none_channel_type_passthrough(self) -> None:
        """Event with channel_type=None overwrites stale current_channel_type."""
        from open_strix.app import OpenStrixApp

        app = OpenStrixApp.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app.current_channel_id = None
        app.current_channel_type = "stale_value"  # leftover from prior turn
        app.current_event_label = None
        app.current_turn_start = None
        app.pending_scheduler_keys = set()
        app._draining = False
        app.log_event = lambda *a, **kw: None

        captured_type = "sentinel"

        async def capture_and_drain(event):
            nonlocal captured_type
            captured_type = app.current_channel_type
            app._draining = True

        app._process_event = capture_and_drain

        event = AgentEvent(
            event_type="poller", prompt="test",
            channel_id="123", channel_type=None,
        )
        app.queue.put_nowait(event)

        await app._event_worker()
        assert captured_type is None


class TestHttpResponseEdgeCases:
    """Verify _send_via_http_handler handles non-standard responses correctly."""

    @staticmethod
    def _make_server(response_body: bytes, status: int = 200, content_type: str = "application/json"):
        """Start an HTTP server returning a fixed response."""
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                received.append(self.rfile.read(length))
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{port}", received

    @staticmethod
    def _make_app(url):
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"send_url": f"{url}/send"}}
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

        return FakeApp()

    @pytest.mark.asyncio
    async def test_html_error_response(self) -> None:
        """HTML response body (e.g. reverse proxy 502) returns success with no event_id."""
        server, url, _ = self._make_server(
            b"<html><body>502 Bad Gateway</body></html>",
            content_type="text/html",
        )
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id is None
            assert chunks == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_empty_response_body(self) -> None:
        """Empty response body returns success with no event_id."""
        server, url, _ = self._make_server(b"")
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id is None
            assert chunks == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_json_array_response(self) -> None:
        """JSON array response returns success with no event_id."""
        server, url, _ = self._make_server(b'[{"ok": true}]')
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id is None
            assert chunks == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_json_null_response(self) -> None:
        """JSON null response returns success with no event_id."""
        server, url, _ = self._make_server(b"null")
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id is None
            assert chunks == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_numeric_event_id_coerced_to_string(self) -> None:
        """Numeric event_id in response is coerced to string."""
        server, url, _ = self._make_server(
            json.dumps({"ok": True, "event_id": 42}).encode()
        )
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id == "42"
            assert chunks == 1
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_empty_event_id_returns_none(self) -> None:
        """Empty string event_id in response is normalized to None."""
        server, url, _ = self._make_server(
            json.dumps({"ok": True, "event_id": ""}).encode()
        )
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is True
            assert event_id is None
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_http_500_returns_failure(self) -> None:
        """HTTP 500 from handler returns failure."""
        server, url, _ = self._make_server(
            b'{"error": "internal"}', status=500,
        )
        try:
            app = self._make_app(url)
            sent, event_id, chunks = await app._send_channel_message(
                channel_id="!room:matrix.org", text="hello",
            )
            assert sent is False
            assert event_id is None
            assert chunks == 0
        finally:
            server.shutdown()

    @pytest.mark.asyncio
    async def test_handler_empty_send_url(self) -> None:
        """Handler with send_url="" returns failure (distinct from missing key)."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"send_url": "", "body_map": "{}"}}
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
            channel_id="!room:matrix.org", text="hello",
        )
        assert sent is False


class TestRoutingEdgeCases:
    """Verify routing logic edge cases in _send_channel_message."""

    @pytest.mark.asyncio
    async def test_explicit_discord_overrides_current_matrix(self) -> None:
        """Explicit channel_type='discord' routes to Discord even when
        current_channel_type='matrix' has a registered handler."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
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
                self.discord_called = False

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

            async def _send_discord_message(self, **kwargs):
                self.discord_called = True
                return True, "123", 1

        app = FakeApp()
        sent, _, _ = await app._send_channel_message(
            channel_id="123456789", text="hi",
            channel_type="discord",  # explicit override to built-in
        )
        assert sent is True
        assert app.discord_called

    @pytest.mark.asyncio
    async def test_unregistered_channel_type_falls_through(self) -> None:
        """Explicit channel_type='slack' with no handler falls through to Discord."""
        from open_strix.discord import DiscordMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin):
            def __init__(self):
                self.config = AppConfig(channel_handlers={})
                self.current_channel_type = None
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []
                self.discord_called = False

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            def is_local_web_channel(self, channel_id):
                return False

            async def _send_discord_message(self, **kwargs):
                self.discord_called = True
                return True, "123", 1

        app = FakeApp()
        sent, _, _ = await app._send_channel_message(
            channel_id="123456789", text="hi",
            channel_type="slack",
        )
        assert sent is True
        assert app.discord_called

    @pytest.mark.asyncio
    async def test_web_channel_misdirected_when_channel_type_set(self) -> None:
        """BUG: When current_channel_type='matrix' (registered handler) and
        channel_id is a web UI channel, the handler check fires first and
        routes to the HTTP handler instead of the web UI.

        This test documents the bug. It will FAIL until the routing logic is
        fixed to check web UI channels before config-driven handlers."""
        from open_strix.discord import DiscordMixin
        from open_strix.web_ui import WebChatMixin

        class FakeApp(_MessageHistoryStub, DiscordMixin, WebChatMixin):
            def __init__(self):
                self.config = AppConfig(
                    web_ui_channel_id="local-web",
                    channel_handlers={
                        "matrix": {"send_url": "http://127.0.0.1:1/send"}
                    },
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []
                self.message_history_all = deque(maxlen=500)
                self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
                self._current_turn_sent_messages = []
                self.web_send_called = False

            def log_event(self, event_type, **payload):
                self.events.append({"type": event_type, **payload})

            async def _send_web_message(self, **kwargs):
                self.web_send_called = True
                return True, "web-123", 1

        app = FakeApp()
        sent, _, _ = await app._send_channel_message(
            channel_id="local-web", text="reply to web user",
        )
        # The reply should go to the web UI, not the matrix bridge
        assert app.web_send_called, (
            "Web UI message was routed to matrix handler instead of web UI"
        )
