#!/usr/bin/env python3
"""Configuration loader for the chainlink backlog worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "chainlink-worker" / "config.toml"
DEFAULT_CHAINLINK_CWD = Path.cwd()
DEFAULT_BRANCH_PREFIX = "chainlink/"


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """One configured worker target."""

    name: str
    repo: Path
    worktree: bool = False
    branch_prefix: str = DEFAULT_BRANCH_PREFIX


@dataclass(frozen=True, slots=True)
class Settings:
    """Shared runtime settings for the worker loop."""

    chainlink_cwd: Path = DEFAULT_CHAINLINK_CWD
    poll_interval_seconds: int = 30
    codex_poll_seconds: int = 10
    max_codex_wait_seconds: int = 1800
    agent_id: str = "backlog-worker"
    rules_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Full app configuration."""

    settings: Settings
    workers: list[WorkerConfig]

    @property
    def worker(self) -> Settings:
        """Compatibility shim for the standalone review poller."""
        return self.settings


def default_config() -> AppConfig:
    """Return the built-in defaults."""
    return AppConfig(settings=Settings(), workers=[])


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config from TOML, or fall back to defaults."""
    config_path = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    defaults = default_config()
    if not config_path.exists():
        return defaults

    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    settings_raw = _as_dict(raw.get("settings"))
    workers_raw = _as_dict(raw.get("workers"))

    settings = Settings(
        chainlink_cwd=_as_path(
            settings_raw.get("chainlink_cwd"),
            defaults.settings.chainlink_cwd,
        ),
        poll_interval_seconds=_as_positive_int(
            settings_raw.get("poll_interval_seconds"),
            defaults.settings.poll_interval_seconds,
        ),
        codex_poll_seconds=_as_positive_int(
            settings_raw.get("codex_poll_seconds"),
            defaults.settings.codex_poll_seconds,
        ),
        max_codex_wait_seconds=_as_positive_int(
            settings_raw.get("max_codex_wait_seconds"),
            defaults.settings.max_codex_wait_seconds,
        ),
        agent_id=_as_text(settings_raw.get("agent_id"), defaults.settings.agent_id),
        rules_dir=_as_optional_path(settings_raw.get("rules_dir")),
    )

    workers: list[WorkerConfig] = []
    for raw_name, raw_worker in workers_raw.items():
        name = str(raw_name).strip()
        if not name:
            continue

        worker_raw = _as_dict(raw_worker)
        repo_value = worker_raw.get("repo")
        if repo_value in (None, ""):
            raise ValueError(f"worker {name!r} is missing required field 'repo'")

        workers.append(
            WorkerConfig(
                name=name,
                repo=_as_path(repo_value, Path()),
                worktree=_as_bool(worker_raw.get("worktree"), False),
                branch_prefix=_as_text(
                    worker_raw.get("branch_prefix"),
                    DEFAULT_BRANCH_PREFIX,
                ),
            )
        )

    return AppConfig(settings=settings, workers=workers)


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_positive_int(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"expected positive integer, got {value!r}")
    return parsed


def _as_text(value: object, default: str) -> str:
    if value in (None, ""):
        return default
    text = str(value).strip()
    return text or default


def _as_bool(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected boolean value, got {value!r}")


def _as_path(value: object, default: Path) -> Path:
    if value in (None, ""):
        return default
    return Path(str(value).strip()).expanduser()


def _as_optional_path(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value).strip()).expanduser()
