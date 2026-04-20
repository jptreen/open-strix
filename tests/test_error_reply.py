"""Tests for the event_worker error-reply path (tony-40w).

Before tony-40w the error fallback only sent an apology to local-web
channels — Matrix/Discord turns that crashed logged
``error_message_sent: false`` and the user got nothing. These tests
exercise the general ``_send_error_reply`` helper that now dispatches
to the right channel handler for any channel_id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.config import AppConfig
from open_strix.models import AgentEvent


def _make_app(*, is_local_web: bool = False):
    """Minimal OpenStrixApp instance wired up for _send_error_reply tests."""
    with patch("open_strix.app.load_config"), \
         patch("open_strix.app.load_dotenv"), \
         patch("open_strix.app.bootstrap_home_repo"), \
         patch("open_strix.app.load_phone_book", return_value={}), \
         patch("open_strix.app.sync_builtin_skills_home"), \
         patch("open_strix.app.Supervisor"):
        from open_strix.app import OpenStrixApp
        app = object.__new__(OpenStrixApp)
        app.config = AppConfig()
        app._send_channel_message = AsyncMock(return_value=(True, "msg-1", 1))
        app._send_web_message = AsyncMock(return_value=(True, "web-1", 1))
        app.log_event = MagicMock()
        # Force the web-channel check to match the parameter.
        app.is_local_web_channel = MagicMock(return_value=is_local_web)
        return app


def _matrix_event() -> AgentEvent:
    return AgentEvent(
        event_type="poller",
        prompt="hello",
        channel_id="!room:matrix.org",
        channel_type="matrix",
    )


def _web_event() -> AgentEvent:
    return AgentEvent(
        event_type="web_message",
        prompt="hello",
        channel_id="local-web",
        channel_type=None,
    )


def _no_channel_event() -> AgentEvent:
    return AgentEvent(event_type="poller", prompt="orphan")


class TestSendErrorReply:
    @pytest.mark.asyncio
    async def test_matrix_event_routes_through_channel_message(self) -> None:
        """Non-web channels dispatch via _send_channel_message with channel_type.

        This is the core tony-40w fix — Matrix events previously hit
        `error_message_sent = False` and bailed without sending anything.
        """
        app = _make_app(is_local_web=False)
        event = _matrix_event()
        exc = RuntimeError("boom")

        sent = await app._send_error_reply(event, exc)

        assert sent is True
        app._send_channel_message.assert_awaited_once()
        kwargs = app._send_channel_message.await_args.kwargs
        assert kwargs["channel_id"] == "!room:matrix.org"
        assert kwargs["channel_type"] == "matrix"
        assert "boom" in kwargs["text"]  # Humanized error text carries the raw message.
        # Web path must NOT have been touched.
        app._send_web_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_local_web_event_routes_through_web_message(self) -> None:
        """Local-web channels still use the in-process web handler."""
        app = _make_app(is_local_web=True)
        event = _web_event()
        exc = RuntimeError("boom")

        sent = await app._send_error_reply(event, exc)

        assert sent is True
        app._send_web_message.assert_awaited_once()
        app._send_channel_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_channel_id_returns_false_silently(self) -> None:
        """Events without a channel_id (e.g. some poller types) can't be replied to."""
        app = _make_app()
        event = _no_channel_event()

        sent = await app._send_error_reply(event, RuntimeError("boom"))

        assert sent is False
        app._send_channel_message.assert_not_awaited()
        app._send_web_message.assert_not_awaited()
        app.log_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_is_logged_and_swallowed(self) -> None:
        """A broken apology path must never crash the worker.

        If the send itself raises, we log error_reply_send_failed and
        return False — the primary error log still fires separately, so
        no information is lost.
        """
        app = _make_app(is_local_web=False)
        app._send_channel_message = AsyncMock(
            side_effect=RuntimeError("bridge is down"),
        )
        event = _matrix_event()
        exc = ValueError("original turn failure")

        sent = await app._send_error_reply(event, exc)

        assert sent is False
        # The failure was logged with BOTH the primary and send errors.
        app.log_event.assert_called_once()
        log_type, log_kwargs = (
            app.log_event.call_args.args[0],
            app.log_event.call_args.kwargs,
        )
        assert log_type == "error_reply_send_failed"
        assert log_kwargs["channel_id"] == "!room:matrix.org"
        assert log_kwargs["channel_type"] == "matrix"
        assert "original turn failure" in log_kwargs["primary_error"]
        assert "bridge is down" in log_kwargs["send_error"]

    @pytest.mark.asyncio
    async def test_send_returning_false_is_propagated(self) -> None:
        """If the handler returns (False, ...) we honor that."""
        app = _make_app(is_local_web=False)
        app._send_channel_message = AsyncMock(return_value=(False, None, 0))
        event = _matrix_event()

        sent = await app._send_error_reply(event, RuntimeError("boom"))

        assert sent is False
        # Not a send_failure (no exception), so no error_reply_send_failed log.
        app.log_event.assert_not_called()
