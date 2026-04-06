"""Tests for YAML block hardening: defensive loading + post-turn validation."""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from open_strix import app as app_mod


def _make_app(tmp_path: Path) -> app_mod.OpenStrixApp:
    (tmp_path / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    return app_mod.OpenStrixApp(tmp_path)


class TestDefensiveBlockLoading:
    """_load_memory_blocks should skip corrupted YAML instead of crashing."""

    def test_corrupted_block_skipped(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        good = blocks_dir / "good.yaml"
        good.write_text(
            yaml.safe_dump({"name": "good", "text": "I work", "sort_order": 0}),
            encoding="utf-8",
        )

        bad = blocks_dir / "bad.yaml"
        bad.write_text("value: 'unterminated string\n  broken: yaml: [", encoding="utf-8")

        blocks = app._load_memory_blocks()
        names = [b["name"] for b in blocks]
        assert "good" in names
        assert "bad" not in names

    def test_corrupted_block_does_not_appear(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        bad = blocks_dir / "broken.yaml"
        bad.write_text("value: 'unterminated\n  broken: [unclosed", encoding="utf-8")

        blocks = app._load_memory_blocks()
        names = [b["name"] for b in blocks]
        assert "broken" not in names

    def test_healthy_blocks_unaffected(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        # Remove default init block
        for f in blocks_dir.glob("*.yaml"):
            f.unlink()

        a = blocks_dir / "alpha.yaml"
        a.write_text(
            yaml.safe_dump({"name": "alpha", "text": "first", "sort_order": 1}),
            encoding="utf-8",
        )
        b = blocks_dir / "beta.yaml"
        b.write_text(
            yaml.safe_dump({"name": "beta", "text": "second", "sort_order": 2}),
            encoding="utf-8",
        )

        blocks = app._load_memory_blocks()
        names = [b["name"] for b in blocks]
        assert names == ["alpha", "beta"]


class TestPostTurnBlockValidation:
    """_validate_memory_blocks detects corrupt blocks for agent self-correction."""

    def test_all_healthy_returns_empty(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        blocks_dir.mkdir(parents=True, exist_ok=True)
        (blocks_dir / "good.yaml").write_text(
            yaml.safe_dump({"name": "good", "text": "works", "sort_order": 0}),
            encoding="utf-8",
        )

        errors = app._validate_memory_blocks()
        assert errors == []

    def test_corrupt_block_detected(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        (blocks_dir / "good.yaml").write_text(
            yaml.safe_dump({"name": "good", "text": "works", "sort_order": 0}),
            encoding="utf-8",
        )
        (blocks_dir / "bad.yaml").write_text(
            "value: 'unterminated string\n  broken: yaml: [",
            encoding="utf-8",
        )

        errors = app._validate_memory_blocks()
        assert len(errors) == 1
        assert "bad.yaml" in errors[0]

    def test_non_dict_block_detected(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        (blocks_dir / "listblock.yaml").write_text("- item1\n- item2\n", encoding="utf-8")

        errors = app._validate_memory_blocks()
        error_text = "\n".join(errors)
        assert "listblock.yaml" in error_text
        assert "mapping" in error_text

    def test_multiple_corrupt_blocks(self, tmp_path: Path) -> None:
        app = _make_app(tmp_path)
        blocks_dir = app.layout.blocks_dir

        # Remove default init block
        for f in blocks_dir.glob("*.yaml"):
            f.unlink()

        (blocks_dir / "bad1.yaml").write_text("': broken", encoding="utf-8")
        (blocks_dir / "bad2.yaml").write_text("- not a dict\n", encoding="utf-8")
        (blocks_dir / "good.yaml").write_text(
            yaml.safe_dump({"name": "good", "text": "ok", "sort_order": 0}),
            encoding="utf-8",
        )

        errors = app._validate_memory_blocks()
        assert len(errors) == 2
        filenames = [e.split(":")[0] for e in errors]
        assert "bad1.yaml" in filenames
        assert "bad2.yaml" in filenames
