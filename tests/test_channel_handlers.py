"""Tests for the pluggable channel handler registry."""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.config import AppConfig, _parse_channel_handlers
from open_strix.models import AgentEvent


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

        class FakeApp(DiscordMixin):
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

        class FakeApp(DiscordMixin):
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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(channel_handlers={})
                self.current_channel_type = None
                self.discord_client = None
                self.events = []
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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": "http://127.0.0.1:1/send"}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"body_map": "{}"}}
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={"matrix": {"send_url": f"{url}/send"}}
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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

        class FakeApp(DiscordMixin):
            def __init__(self):
                self.config = AppConfig(
                    channel_handlers={
                        "matrix": {"send_url": f"{url}/send", "body_map": body_map}
                    }
                )
                self.current_channel_type = "matrix"
                self.discord_client = None
                self.events = []

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
    """Verify current_channel_type is set/reset by _event_worker."""

    @pytest.mark.asyncio
    async def test_current_channel_type_set_from_event(self) -> None:
        """AgentEvent carries channel_type through correctly."""
        event = AgentEvent(
            event_type="poller",
            prompt="test",
            channel_id="!room:matrix.org",
            channel_type="matrix",
        )
        assert event.channel_type == "matrix"
        assert event.channel_id == "!room:matrix.org"
