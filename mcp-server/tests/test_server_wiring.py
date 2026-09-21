"""Guards on server.py's wiring to the MCP SDK.

This is the file that would have caught the v1 -> v2 break: the SDK renamed
`FastMCP` to `MCPServer` and moved it out of `mcp.server.fastmcp`, so a lock
refresh alone turned `import server` into a ModuleNotFoundError. Nothing here
mocks the SDK — the point is to exercise the real import path and the real
registration, so the next SDK bump fails in CI instead of at runtime.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from mcp.server.mcpserver import MCPServer  # type: ignore[import-not-found]

# Every tool the server is expected to expose. Adding a tool should be a
# deliberate edit here, and removing one should never be silent.
EXPECTED_TOOLS = {
    "check_connectivity",
    "compare_configs",
    "get_all_errors",
    "get_device_config",
    "get_device_logs",
    "get_device_status",
    "get_service_status",
    "list_devices",
    "restart_service",
    "run_diagnostics",
    "search_logs",
}


@pytest.fixture
def loaded_server(tmp_path, monkeypatch):
    """Import server.py fresh against a throwaway config.

    server.py loads config and builds its SSH pool at import time, so the test
    points PULSE_MCP_CONFIG at a temp file and drops the module from
    sys.modules first — otherwise it would pick up whatever pulse-mcp.conf
    happens to sit in the developer's repo root.
    """
    conf = tmp_path / "pulse-mcp.conf"
    conf.write_text(
        json.dumps(
            {
                "ssh": {"user": "pulse", "remote_path": "/opt/pulse-os"},
                "devices": ["pulse-office", "pulse-kitchen"],
                "devices_file": "",
            }
        )
    )
    monkeypatch.setenv("PULSE_MCP_CONFIG", str(conf))
    for name in ("server", "config", "ssh"):
        sys.modules.pop(name, None)
    module = importlib.import_module("server")
    yield module
    sys.modules.pop("server", None)


def test_server_builds_on_the_v2_sdk_class(loaded_server):
    """The rename guard: FastMCP is gone in SDK v2, MCPServer replaced it."""
    assert isinstance(loaded_server.mcp, MCPServer)
    assert loaded_server.mcp.name == "pulse-os"


async def test_server_exposes_every_tool(loaded_server):
    names = {tool.name for tool in await loaded_server.mcp.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_every_tool_is_documented(loaded_server):
    """Descriptions are the only thing a model has to choose a tool by."""
    for tool in await loaded_server.mcp.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert tool.input_schema["type"] == "object"


async def test_device_tools_take_a_device_argument(loaded_server):
    """Everything but the fleet-wide tools is scoped to one device."""
    fleet_wide = {"list_devices", "compare_configs"}
    for tool in await loaded_server.mcp.list_tools():
        if tool.name in fleet_wide:
            continue
        assert "device" in tool.input_schema.get("properties", {}), f"{tool.name} takes no device"


def test_config_comes_from_the_environment(loaded_server):
    assert loaded_server.config.devices == ["pulse-office", "pulse-kitchen"]
    assert loaded_server.config.ssh.remote_path == "/opt/pulse-os"
