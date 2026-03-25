"""Tests for configurable subagents."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from open_strix.config import (
    AppConfig,
    RepoLayout,
    SubAgentConfig,
    _parse_subagent_configs,
    load_config,
)


class TestParseSubagentConfigs:
    def test_none_returns_empty(self) -> None:
        assert _parse_subagent_configs(None) == []

    def test_not_list_returns_empty(self) -> None:
        assert _parse_subagent_configs("bad") == []
        assert _parse_subagent_configs(42) == []

    def test_empty_list(self) -> None:
        assert _parse_subagent_configs([]) == []

    def test_skips_non_dict_items(self) -> None:
        assert _parse_subagent_configs(["bad", 42, None]) == []

    def test_skips_items_without_name(self) -> None:
        assert _parse_subagent_configs([{"description": "no name"}]) == []
        assert _parse_subagent_configs([{"name": "", "description": "empty name"}]) == []

    def test_basic_subagent(self) -> None:
        raw = [
            {
                "name": "vision",
                "description": "Describe images cheaply",
                "model": "anthropic:claude-haiku-3-5",
            }
        ]
        result = _parse_subagent_configs(raw)
        assert len(result) == 1
        assert result[0].name == "vision"
        assert result[0].description == "Describe images cheaply"
        assert result[0].model == "anthropic:claude-haiku-3-5"

    def test_defaults_for_optional_fields(self) -> None:
        raw = [{"name": "fast", "description": "Fast agent"}]
        result = _parse_subagent_configs(raw)
        assert len(result) == 1
        assert result[0].model == ""
        assert result[0].system_prompt == ""
        assert result[0].allowed_tools is None

    def test_multiple_subagents(self) -> None:
        raw = [
            {"name": "vision", "description": "Image tasks", "model": "anthropic:claude-haiku-3-5"},
            {"name": "fast", "description": "Quick tasks", "model": "anthropic:claude-haiku-3-5"},
        ]
        result = _parse_subagent_configs(raw)
        assert len(result) == 2
        assert result[0].name == "vision"
        assert result[1].name == "fast"

    def test_custom_system_prompt(self) -> None:
        raw = [
            {
                "name": "vision",
                "description": "Image tasks",
                "system_prompt": "You are a vision specialist.",
            }
        ]
        result = _parse_subagent_configs(raw)
        assert result[0].system_prompt == "You are a vision specialist."

    def test_strips_whitespace(self) -> None:
        raw = [{"name": "  vision  ", "description": "  Image tasks  ", "model": "  anthropic:claude-haiku-3-5  "}]
        result = _parse_subagent_configs(raw)
        assert result[0].name == "vision"
        assert result[0].description == "Image tasks"
        assert result[0].model == "anthropic:claude-haiku-3-5"


class TestAppConfigSubagents:
    def test_default_empty(self) -> None:
        config = AppConfig()
        assert config.subagents == []

    def test_with_subagents(self) -> None:
        config = AppConfig(
            subagents=[
                SubAgentConfig(name="vision", description="Image tasks", model="anthropic:claude-haiku-3-5"),
            ]
        )
        assert len(config.subagents) == 1
        assert config.subagents[0].name == "vision"


class TestLoadConfigSubagents:
    def test_loads_subagents_from_config(self, tmp_path: Path) -> None:
        config_data = {
            "model": "test-model",
            "subagents": [
                {
                    "name": "vision",
                    "description": "Cheap image description agent",
                    "model": "anthropic:claude-haiku-3-5",
                    "system_prompt": "Describe images concisely.",
                },
                {
                    "name": "fast",
                    "description": "Quick tasks with a cheap model",
                    "model": "anthropic:claude-haiku-3-5",
                },
            ],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert len(config.subagents) == 2
        assert config.subagents[0].name == "vision"
        assert config.subagents[0].model == "anthropic:claude-haiku-3-5"
        assert config.subagents[0].system_prompt == "Describe images concisely."
        assert config.subagents[1].name == "fast"

    def test_no_subagents_key(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.subagents == []

    def test_invalid_subagents_value(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model", "subagents": "not-a-list"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.subagents == []
