"""Worker behavior when a model request timeout propagates from a turn.

tony-9k6: a per-request timeout fires inside the model SDK/client after
retries are exhausted or the client gives up. The event worker must:

  - Set ``_last_turn_failure`` so the next turn knows the previous one died.
  - Log a structured warning with ``warning_type=model_request_timeout``.
  - Continue the worker loop (do not crash, do not wedge).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from open_strix.models import AgentEvent


@pytest.fixture
def app():
    with patch("open_strix.app.load_config"), \
         patch("open_strix.app.load_dotenv"), \
         patch("open_strix.app.bootstrap_home_repo"), \
         patch("open_strix.app.load_phone_book", return_value={}), \
         patch("open_strix.app.sync_builtin_skills_home"), \
         patch("open_strix.app.Supervisor"):
        from open_strix.app import OpenStrixApp
        instance = object.__new__(OpenStrixApp)
        instance.queue = asyncio.Queue()
        instance._draining = False
        instance.worker_task = None
        instance.discord_client = None
        instance.log_event = MagicMock()
        instance.current_channel_id = None
        instance.current_channel_type = None
        instance.current_event_label = None
        instance.current_turn_start = None
        instance.pending_scheduler_keys = set()
        instance._last_turn_failure = None
        # The generic ``except Exception`` branch reacts/apologizes; the
        # timeout handling path must NOT — these stubs catch regressions
        # that fall through to user-facing apology behavior.
        instance._react_to_latest_message = AsyncMock(return_value=False)
        instance._send_error_reply = AsyncMock(return_value=False)
        return instance


def _make_anthropic_timeout_error() -> anthropic.APITimeoutError:
    """Construct an APITimeoutError without hitting the network."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APITimeoutError(request=request)


def _make_httpx_read_timeout_error() -> httpx.ReadTimeout:
    """Construct a provider-neutral read timeout without hitting the network."""
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.ReadTimeout("read timed out", request=request)


def _drain_after_warning(app, warning_type: str) -> None:
    """Make ``app.log_event`` flip ``_draining=True`` once a given warning fires.

    The except branch logs *before* the worker's ``finally`` block, so
    setting ``_draining`` here causes the finally to break cleanly out
    of the worker loop after a single processed event.
    """
    base = MagicMock()

    def _side_effect(*args, **kwargs):
        base(*args, **kwargs)
        if kwargs.get("warning_type") == warning_type:
            app._draining = True

    app.log_event = MagicMock(side_effect=_side_effect)
    app.log_event._base_recorder = base  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_model_timeout_error_logs_warning(app):
    """Timeout errors log ``model_request_timeout``."""
    timeout_exc = _make_anthropic_timeout_error()

    async def _raise_timeout(_event: AgentEvent) -> None:
        raise timeout_exc

    app._process_event = _raise_timeout
    _drain_after_warning(app, "model_request_timeout")

    event = AgentEvent(event_type="test_msg", prompt="hi", channel_id="c1")
    app.queue.put_nowait(event)

    await asyncio.wait_for(app._event_worker(), timeout=2)

    warning_calls = [
        call for call in app.log_event.call_args_list
        if call.args and call.args[0] == "warning"
        and call.kwargs.get("warning_type") == "model_request_timeout"
    ]
    assert len(warning_calls) == 1, (
        f"expected exactly one model_request_timeout warning, "
        f"got: {app.log_event.call_args_list}"
    )
    call = warning_calls[0]
    assert call.kwargs["where"] == "event_worker"
    assert call.kwargs["source_event_type"] == "test_msg"
    assert call.kwargs["channel_id"] == "c1"
    assert "traceback" in call.kwargs


@pytest.mark.asyncio
async def test_model_timeout_error_sets_last_turn_failure(app):
    """The next turn's prompt must surface the timeout via ``_last_turn_failure``."""
    timeout_exc = _make_anthropic_timeout_error()

    async def _raise_timeout(_event: AgentEvent) -> None:
        raise timeout_exc

    app._process_event = _raise_timeout
    _drain_after_warning(app, "model_request_timeout")

    event = AgentEvent(event_type="test_msg", prompt="hi", channel_id="c1")
    app.queue.put_nowait(event)

    await asyncio.wait_for(app._event_worker(), timeout=2)

    assert app._last_turn_failure is not None
    assert "timeout" in app._last_turn_failure.lower()
    assert "transient" in app._last_turn_failure.lower()


@pytest.mark.asyncio
async def test_model_timeout_error_does_not_send_error_reply(app):
    """Timeouts use the warning path, not the user-facing apology path."""
    timeout_exc = _make_anthropic_timeout_error()

    async def _raise_timeout(_event: AgentEvent) -> None:
        raise timeout_exc

    app._process_event = _raise_timeout
    _drain_after_warning(app, "model_request_timeout")

    event = AgentEvent(event_type="test_msg", prompt="hi", channel_id="c1")
    app.queue.put_nowait(event)

    await asyncio.wait_for(app._event_worker(), timeout=2)

    app._send_error_reply.assert_not_called()
    app._react_to_latest_message.assert_not_called()


@pytest.mark.asyncio
async def test_worker_continues_after_timeout(app):
    """A timeout drops the bad event but the worker keeps draining the queue."""
    timeout_exc = _make_anthropic_timeout_error()
    processed: list[str] = []

    async def _maybe_raise(event: AgentEvent) -> None:
        processed.append(event.event_type)
        if event.event_type == "bad":
            raise timeout_exc
        # The "good" event triggers shutdown so the test terminates.
        if event.event_type == "good":
            app._draining = True

    app._process_event = _maybe_raise

    app.queue.put_nowait(AgentEvent(event_type="bad", prompt="x", channel_id="c1"))
    app.queue.put_nowait(AgentEvent(event_type="good", prompt="y", channel_id="c1"))

    await asyncio.wait_for(app._event_worker(), timeout=2)

    assert processed == ["bad", "good"], (
        f"worker should drain past the timeout to the next event; got {processed}"
    )


@pytest.mark.asyncio
async def test_httpx_timeout_uses_same_warning_path(app):
    """Provider-neutral timeout classes use the same worker recovery path."""
    timeout_exc = _make_httpx_read_timeout_error()

    async def _raise_timeout(_event: AgentEvent) -> None:
        raise timeout_exc

    app._process_event = _raise_timeout
    _drain_after_warning(app, "model_request_timeout")

    event = AgentEvent(event_type="test_msg", prompt="hi", channel_id="c1")
    app.queue.put_nowait(event)

    await asyncio.wait_for(app._event_worker(), timeout=2)

    warning_calls = [
        call for call in app.log_event.call_args_list
        if call.args and call.args[0] == "warning"
        and call.kwargs.get("warning_type") == "model_request_timeout"
    ]
    assert len(warning_calls) == 1
    assert app._last_turn_failure is not None
