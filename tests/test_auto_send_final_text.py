"""Tests for final_text auto-send logic."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.config import AppConfig
from open_strix.models import AgentEvent


def _make_app():
    """Create a minimal mock app with the fields _auto_send_final_text needs."""
    with patch("open_strix.app.load_config"), \
         patch("open_strix.app.load_dotenv"), \
         patch("open_strix.app.bootstrap_home_repo"), \
         patch("open_strix.app.load_phone_book", return_value={}), \
         patch("open_strix.app.sync_builtin_skills_home"), \
         patch("open_strix.app.Supervisor"):
        from open_strix.app import OpenStrixApp
        app = object.__new__(OpenStrixApp)
        app.config = AppConfig(auto_send_final_text=True)
        app._withhold_final_text = False
        app._send_message_circuit_breaker_active = False
        app._send_channel_message = AsyncMock(return_value=(True, "msg-1", 1))
        app.log_event = MagicMock()
        return app


def _event(channel_id="matrix-room-1", channel_type="matrix"):
    return AgentEvent(
        event_type="poller",
        prompt="hello",
        channel_id=channel_id,
        channel_type=channel_type,
    )


@pytest.mark.asyncio
async def test_auto_sends_final_text_by_default():
    app = _make_app()
    event = _event()
    await app._auto_send_final_text(event, "Here is your answer.")

    app._send_channel_message.assert_awaited_once_with(
        channel_id="matrix-room-1",
        text="Here is your answer.",
        channel_type="matrix",
    )
    app.log_event.assert_any_call(
        "agent_final_text_auto_sent",
        channel_id="matrix-room-1",
        final_text="Here is your answer.",
    )


@pytest.mark.asyncio
async def test_withheld_when_flag_set():
    app = _make_app()
    app._withhold_final_text = True
    event = _event()
    await app._auto_send_final_text(event, "secret thoughts")

    app._send_channel_message.assert_not_awaited()
    app.log_event.assert_any_call(
        "agent_final_text_withheld",
        channel_id="matrix-room-1",
        final_text="secret thoughts",
    )


@pytest.mark.asyncio
async def test_skipped_when_config_disabled():
    app = _make_app()
    app.config = AppConfig(auto_send_final_text=False)
    event = _event()
    await app._auto_send_final_text(event, "should not send")

    app._send_channel_message.assert_not_awaited()
    app.log_event.assert_any_call(
        "agent_final_text_discarded",
        reason="disabled_by_config",
        channel_id="matrix-room-1",
        final_text="should not send",
    )


@pytest.mark.asyncio
async def test_skipped_when_no_channel_id():
    app = _make_app()
    event = _event(channel_id=None)
    await app._auto_send_final_text(event, "background task output")

    app._send_channel_message.assert_not_awaited()
    app.log_event.assert_any_call(
        "agent_final_text_discarded",
        reason="no_channel_id",
        final_text="background task output",
    )


@pytest.mark.asyncio
async def test_skipped_when_circuit_breaker_active():
    app = _make_app()
    app._send_message_circuit_breaker_active = True
    event = _event()
    await app._auto_send_final_text(event, "looping text")

    app._send_channel_message.assert_not_awaited()
    app.log_event.assert_any_call(
        "agent_final_text_discarded",
        reason="circuit_breaker_active",
        channel_id="matrix-room-1",
        final_text="looping text",
    )


@pytest.mark.asyncio
async def test_skipped_when_empty_text():
    app = _make_app()
    event = _event()
    await app._auto_send_final_text(event, "")

    app._send_channel_message.assert_not_awaited()
    app.log_event.assert_any_call(
        "agent_final_text_empty",
        channel_id="matrix-room-1",
    )


@pytest.mark.asyncio
async def test_send_failure_is_logged_not_raised():
    app = _make_app()
    app._send_channel_message = AsyncMock(side_effect=RuntimeError("Discord is down"))
    event = _event()

    # Should not raise
    await app._auto_send_final_text(event, "will fail to send")

    app.log_event.assert_any_call(
        "agent_final_text_send_failed",
        channel_id="matrix-room-1",
        error="Discord is down",
        final_text="will fail to send",
    )
