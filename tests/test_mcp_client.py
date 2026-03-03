"""Tests for MCP client integration."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.mcp_client import (
    MCPConnection,
    MCPManager,
    MCPServerConfig,
    _bridge_mcp_tool,
    parse_mcp_server_configs,
)


# ---------- MCPServerConfig ----------


class TestMCPServerConfig:
    def test_from_dict_basic(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test-server",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-test"],
        })
        assert cfg.name == "test-server"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@anthropic/mcp-server-test"]
        assert cfg.env is None

    def test_from_dict_with_env(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "python",
            "args": ["server.py"],
            "env": {"FOO": "bar", "BAZ": "qux"},
        })
        assert cfg.env == {"FOO": "bar", "BAZ": "qux"}

    def test_from_dict_env_expansion(self):
        with patch.dict(os.environ, {"MY_KEY": "secret123"}):
            cfg = MCPServerConfig.from_dict({
                "name": "test",
                "command": "npx",
                "args": [],
                "env": {"API_KEY": "${MY_KEY}"},
            })
        assert cfg.env == {"API_KEY": "secret123"}

    def test_from_dict_env_expansion_missing(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
            "args": [],
            "env": {"API_KEY": "${NONEXISTENT_VAR_12345}"},
        })
        assert cfg.env == {"API_KEY": ""}

    def test_from_dict_missing_name(self):
        with pytest.raises(ValueError, match="requires a 'name'"):
            MCPServerConfig.from_dict({"command": "npx"})

    def test_from_dict_missing_command(self):
        with pytest.raises(ValueError, match="requires a 'command'"):
            MCPServerConfig.from_dict({"name": "test"})

    def test_from_dict_empty_args(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
        })
        assert cfg.args == []

    def test_from_dict_args_coerced_to_str(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
            "args": [123, True],
        })
        assert cfg.args == ["123", "True"]


# ---------- parse_mcp_server_configs ----------


class TestParseMCPServerConfigs:
    def test_none(self):
        assert parse_mcp_server_configs(None) == []

    def test_empty_list(self):
        assert parse_mcp_server_configs([]) == []

    def test_not_a_list(self):
        assert parse_mcp_server_configs("bad") == []
        assert parse_mcp_server_configs(42) == []

    def test_valid_configs(self):
        raw = [
            {"name": "a", "command": "echo", "args": ["hello"]},
            {"name": "b", "command": "node", "args": ["server.js"]},
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 2
        assert configs[0].name == "a"
        assert configs[1].name == "b"

    def test_skips_invalid(self, capsys):
        raw = [
            {"name": "good", "command": "echo"},
            {"command": "missing-name"},  # invalid
            {"name": "also-good", "command": "node"},
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 2
        assert configs[0].name == "good"
        assert configs[1].name == "also-good"

    def test_skips_non_dict(self):
        raw = [
            {"name": "good", "command": "echo"},
            "not-a-dict",
            42,
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 1


# ---------- _bridge_mcp_tool ----------


class TestBridgeMCPTool:
    def test_basic_tool(self):
        session = MagicMock()
        tool = _bridge_mcp_tool(
            server_name="brave",
            tool_name="search",
            description="Search the web",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            session=session,
        )
        assert tool.name == "mcp_brave_search"
        assert "Search the web" in tool.description
        assert "query" in tool.description

    def test_empty_schema(self):
        session = MagicMock()
        tool = _bridge_mcp_tool(
            server_name="test",
            tool_name="ping",
            description="Ping the server",
            input_schema={"type": "object"},
            session=session,
        )
        assert tool.name == "mcp_test_ping"
        assert tool.description == "Ping the server"


# ---------- MCPConnection ----------


class TestMCPConnection:
    @pytest.mark.asyncio
    async def test_discover_tools(self):
        mock_session = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.description = "Search things"
        mock_tool.inputSchema = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
        }

        mock_result = MagicMock()
        mock_result.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_result)

        config = MCPServerConfig(name="test", command="echo", args=[])
        conn = MCPConnection(config=config, session=mock_session)
        tools = await conn.discover_tools()

        assert len(tools) == 1
        assert tools[0].name == "mcp_test_search"
        assert conn.tool_names == ["search"]


# ---------- MCPManager ----------


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_start_servers_empty(self):
        manager = MCPManager()
        tools = await manager.start_servers([])
        assert tools == []
        assert manager.connections == []
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_connections(self):
        manager = MCPManager()
        # Simulate having a connection.
        mock_conn = MagicMock()
        manager.connections.append(mock_conn)
        await manager.shutdown()
        assert manager.connections == []

    @pytest.mark.asyncio
    async def test_failed_server_skipped(self):
        manager = MCPManager()
        log_calls: list[tuple[str, dict[str, Any]]] = []

        def fake_log(event_type: str, **kwargs: Any) -> None:
            log_calls.append((event_type, kwargs))

        # This will fail because "nonexistent-binary-xyz" doesn't exist.
        configs = [
            MCPServerConfig(
                name="bad-server",
                command="nonexistent-binary-xyz",
                args=[],
            ),
        ]
        tools = await manager.start_servers(configs, log_fn=fake_log)
        assert tools == []
        assert manager.connections == []
        # Should have logged the failure.
        assert any(et == "mcp_server_failed" for et, _ in log_calls)
        await manager.shutdown()


# ---------- Config integration ----------


class TestConfigIntegration:
    def test_appconfig_has_mcp_servers(self):
        from open_strix.config import AppConfig
        config = AppConfig()
        assert config.mcp_servers == []

    def test_load_config_with_mcp_servers(self, tmp_path):
        from open_strix.config import RepoLayout, bootstrap_home_repo, load_config

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")

        # Add mcp_servers to config.
        config_data = {
            "model": "test-model",
            "mcp_servers": [
                {"name": "test", "command": "echo", "args": ["hello"]},
            ],
        }
        layout.config_file.write_text(
            __import__("yaml").safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )
        config = load_config(layout)
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "test"

    def test_load_config_without_mcp_servers(self, tmp_path):
        from open_strix.config import RepoLayout, bootstrap_home_repo, load_config

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")
        config = load_config(layout)
        assert config.mcp_servers == []
