"""Tests for the turn-timing instrumentation emitted by _process_event (#91)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import pytest
from langchain_core.messages import AIMessage

import open_strix.app as app_mod


class DummyAgent:
    def __init__(self) -> None:
        self.invoke_count = 0

    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        self.invoke_count += 1
        return {"messages": [AIMessage(content="ok")]}


def _build_app(tmp_path: Path, monkeypatch, *, agent: DummyAgent) -> app_mod.OpenStrixApp:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: agent)
    app = app_mod.OpenStrixApp(tmp_path)

    # Avoid real git/subprocess work inside the turn; we only care about timing capture.
    async def _noop_git(_event: Any) -> str:
        return "skip: test"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    return app


def _collect_events(app: app_mod.OpenStrixApp) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = app.log_event

    def _capture(event_type: str, **payload: Any) -> None:
        calls.append({"type": event_type, **payload})
        original(event_type, **payload)

    app.log_event = _capture  # type: ignore[assignment]
    return calls


def test_turn_timing_event_emitted_with_breakdown(tmp_path: Path, monkeypatch) -> None:
    agent = DummyAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)
    events = _collect_events(app)

    event = app_mod.AgentEvent(
        event_type="discord_message",
        prompt="hello",
        channel_id="123",
        author="alice",
    )

    asyncio.run(app._process_event(event))

    timing_events = [e for e in events if e["type"] == "turn_timing"]
    assert len(timing_events) == 1
    timing = timing_events[0]
    for key in (
        "total_seconds",
        "context_load_seconds",
        "agent_invoke_seconds",
        "block_validation_seconds",
        "block_repair_invoke_seconds",
        "git_sync_seconds",
        "repair_invoke_count",
    ):
        assert key in timing, f"missing {key} in turn_timing payload"
    assert timing["source_event_type"] == "discord_message"
    assert timing["channel_id"] == "123"
    assert timing["repair_invoke_count"] == 0
    assert timing["total_seconds"] >= 0.0
    assert timing["context_load_seconds"] >= 0.0
    assert timing["agent_invoke_seconds"] >= 0.0
    assert timing["block_repair_invoke_seconds"] == 0.0
    # Components should roughly sum to no more than the total (allow slack for
    # untracked gaps like session log writes).
    component_sum = (
        timing["context_load_seconds"]
        + timing["agent_invoke_seconds"]
        + timing["block_validation_seconds"]
        + timing["block_repair_invoke_seconds"]
        + timing["git_sync_seconds"]
    )
    assert component_sum <= timing["total_seconds"] + 0.05


def test_turn_timing_emitted_even_when_agent_raises(tmp_path: Path, monkeypatch) -> None:
    class BoomAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("boom")

    agent = BoomAgent()
    app = _build_app(tmp_path, monkeypatch, agent=agent)  # type: ignore[arg-type]
    events = _collect_events(app)

    event = app_mod.AgentEvent(
        event_type="discord_message",
        prompt="hello",
        channel_id="456",
        author="alice",
    )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(app._process_event(event))

    timing_events = [e for e in events if e["type"] == "turn_timing"]
    assert len(timing_events) == 1
    timing = timing_events[0]
    # Context load ran before the crash; agent invoke crashed so its timing
    # remains the initial 0.0 sentinel.
    assert timing["context_load_seconds"] >= 0.0
    assert timing["agent_invoke_seconds"] == 0.0
    assert timing["repair_invoke_count"] == 0
    assert timing["total_seconds"] >= timing["context_load_seconds"]
