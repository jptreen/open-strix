"""Test that create_memory_block creates blocks/ when missing (issue #85)."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import open_strix.app as app_mod


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> app_mod.OpenStrixApp:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
    # config.yaml without "blocks" in folders — reproduces the reporter's setup.
    (tmp_path / "config.yaml").write_text(
        "model: test-model\n"
        "folders:\n"
        "  state: rw\n"
        "  skills: rw\n"
        "  scripts: ro\n"
        "  logs: ro\n",
        encoding="utf-8",
    )
    return app_mod.OpenStrixApp(tmp_path)


@pytest.mark.asyncio
async def test_create_memory_block_succeeds_when_blocks_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(tmp_path, monkeypatch)
    blocks_dir = app.layout.blocks_dir

    # Bootstrap may have created blocks_dir; remove it to reproduce issue #85 exactly.
    if blocks_dir.exists():
        shutil.rmtree(blocks_dir)
    assert not blocks_dir.exists()

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["create_memory_block"].ainvoke(
        {"block_id": "goals", "name": "Goals", "text": "test body", "sort_order": 10}
    )

    assert "Created memory block 'goals'" in result
    assert (blocks_dir / "goals.yaml").exists()
