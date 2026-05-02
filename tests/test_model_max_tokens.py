"""Tests for model_max_output_tokens config + _build_chat_model wiring.

langchain-anthropic falls back to 4096 max output tokens for any model not in
its Claude-only profile table. That truncates tool_use args mid-stream for
non-Claude models like MiniMax-M2.5, dropping large write_file content.
These tests pin the explicit max_tokens pass-through.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import open_strix.app as app_mod
from open_strix.config import (
    DEFAULT_MODEL_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS,
    AppConfig,
    RepoLayout,
    bootstrap_home_repo,
    load_config,
)


def test_app_config_default_max_output_tokens() -> None:
    config = AppConfig()
    assert config.model_max_output_tokens == DEFAULT_MODEL_MAX_OUTPUT_TOKENS
    assert config.model_max_output_tokens == 32768


def test_load_config_overrides_max_output_tokens(tmp_path: Path) -> None:
    config_data = {"model": "MiniMax-M2.5", "model_max_output_tokens": 65536}
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    config = load_config(layout)
    assert config.model_max_output_tokens == 65536


def test_load_config_defaults_max_output_tokens_when_missing(tmp_path: Path) -> None:
    config_data = {"model": "MiniMax-M2.5"}
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    config = load_config(layout)
    assert config.model_max_output_tokens == DEFAULT_MODEL_MAX_OUTPUT_TOKENS


def test_bootstrap_writes_max_output_tokens_default(tmp_path: Path) -> None:
    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    bootstrap_home_repo(layout, checkpoint_text="test")
    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert loaded["model_max_output_tokens"] == DEFAULT_MODEL_MAX_OUTPUT_TOKENS


def test_build_chat_model_passes_max_tokens(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model_name: str, **kwargs: Any) -> str:
        captured["model_name"] = model_name
        captured.update(kwargs)
        return "stub-model"

    monkeypatch.setattr(app_mod, "init_chat_model", fake_init_chat_model)

    result = app_mod._build_chat_model(
        "anthropic:MiniMax-M2.5",
        max_retries=3,
        max_tokens=12345,
    )

    assert result == "stub-model"
    assert captured["model_name"] == "anthropic:MiniMax-M2.5"
    assert captured["max_retries"] == 3
    assert captured["max_tokens"] == 12345


def test_build_chat_model_defaults_to_32768(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model_name: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "stub-model"

    monkeypatch.setattr(app_mod, "init_chat_model", fake_init_chat_model)

    app_mod._build_chat_model("anthropic:MiniMax-M2.5")
    assert captured["max_tokens"] == DEFAULT_MODEL_MAX_OUTPUT_TOKENS


def test_build_chat_model_clamps_non_positive_max_tokens(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model_name: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "stub-model"

    monkeypatch.setattr(app_mod, "init_chat_model", fake_init_chat_model)

    app_mod._build_chat_model("anthropic:MiniMax-M2.5", max_tokens=0)
    assert captured["max_tokens"] == 1


def test_build_chat_model_passes_request_timeout(monkeypatch) -> None:
    """tony-9k6: per-request timeout must reach LangChain.

    LangChain providers commonly accept ``timeout``. For Anthropic-compatible
    models, langchain-anthropic aliases it to the SDK request timeout.
    """
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model_name: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "stub-model"

    monkeypatch.setattr(app_mod, "init_chat_model", fake_init_chat_model)

    app_mod._build_chat_model(
        "anthropic:claude-sonnet-4-6",
        request_timeout_seconds=42.5,
    )
    assert captured["timeout"] == 42.5


def test_build_chat_model_request_timeout_default(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model_name: str, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "stub-model"

    monkeypatch.setattr(app_mod, "init_chat_model", fake_init_chat_model)

    app_mod._build_chat_model("anthropic:claude-sonnet-4-6")
    assert captured["timeout"] == DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS


def test_app_config_default_request_timeout() -> None:
    config = AppConfig()
    assert config.model_request_timeout_seconds == DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
    assert config.model_request_timeout_seconds == 60.0


def test_load_config_overrides_request_timeout(tmp_path: Path) -> None:
    config_data = {
        "model": "claude-sonnet-4-6",
        "model_request_timeout_seconds": 120.0,
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    config = load_config(layout)
    assert config.model_request_timeout_seconds == 120.0


def test_load_config_request_timeout_falls_back_on_invalid(tmp_path: Path) -> None:
    """Non-positive, non-finite, or unparseable values fall back."""
    for bad in (0, -5, "not-a-number", "nan", ".nan", "inf", ".inf", None):
        config_data: dict[str, Any] = {"model": "claude-sonnet-4-6"}
        if bad is not None:
            config_data["model_request_timeout_seconds"] = bad
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.model_request_timeout_seconds == DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS


def test_bootstrap_writes_request_timeout_default(tmp_path: Path) -> None:
    layout = RepoLayout(home=tmp_path, state_dir_name="state")
    bootstrap_home_repo(layout, checkpoint_text="test")
    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert (
        loaded["model_request_timeout_seconds"]
        == DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
    )
