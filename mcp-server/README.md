# Pulse OS MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects to your Pulse devices for diagnostics and log reading. Runs on your development machine and connects to devices remotely via SSH.

## Setup

### 1. Install prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- SSH key-based access to your Pulse devices

### 2. Configure

Copy the sample config and edit it with your settings:

```bash
cp pulse-mcp.conf.sample pulse-mcp.conf
```

Edit `pulse-mcp.conf` with your MQTT broker host and SSH key path:

```json
{
  "mqtt": {
    "host": "mosquitto.local",
    "port": 1883
  },
  "ssh": {
    "user": "pulse",
    "key_path": "~/.ssh/id_ed25519",
    "remote_path": "/opt/pulse-os",
    "timeout": 10
  },
  "devices_file": "pulse-devices.conf"
}
```

Make sure `pulse-devices.conf` exists in the repo root with your device hostnames (one per line):

```
pulse-office
pulse-kitchen
pulse-bedroom
```

### 3. Activate in Claude Code

Create `.mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "pulse-os": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "mcp-server", "run", "server.py"]
    }
  }
}
```

Restart Claude Code. The MCP server will start automatically when you open the project.

### 4. Verify

Ask Claude: "list my pulse devices" — it should SSH into each device and report reachability.

## Available Tools

### Logs
| Tool | Description |
|------|-------------|
| `get_device_logs` | Read journal logs for a service (filterable by time, priority, grep pattern) |
| `get_all_errors` | All error-level entries across all Pulse services |
| `search_logs` | Regex search across service logs |

### Status
| Tool | Description |
|------|-------------|
| `list_devices` | All configured devices with SSH reachability |
| `get_device_status` | Services, OS info, CPU temp, memory, disk, uptime |
| `get_service_status` | Full `systemctl status` output for a service |

### Configuration
| Tool | Description |
|------|-------------|
| `get_device_config` | Device config values (secrets masked) |
| `compare_configs` | Diff config across devices, flag unexpected differences |

### Diagnostics
| Tool | Description |
|------|-------------|
| `run_diagnostics` | Run `verify-conf.py` remotely (MQTT, Wyoming, HA checks) |
| `check_connectivity` | SSH, service states, MQTT broker, recent error count |
| `restart_service` | Restart a Pulse service (allowlisted names only) |

## Tests

```bash
uv run --directory mcp-server --frozen --dev pytest    # from the repo root
uv run --frozen --dev pytest                           # from mcp-server/
```

This is a separate uv project with its own lockfile, so the repo-root `uv run
pytest` does not reach it — CI runs it as its own step in `build.yaml`.

No test talks to a real device: `tests/conftest.py` swaps in a `FakeSSH` that
records the commands a tool builds and replays canned output, so the suite
covers both the command (quoting, clamping, unit names, the restart allowlist)
and how the result is rendered.

`tests/test_server_wiring.py` is the SDK guard. It imports `server.py` for
real and asserts all 11 tools register, which is what catches a breaking SDK
release — the v1 → v2 upgrade renamed `FastMCP` to `MCPServer` and moved it out
of `mcp.server.fastmcp`, so a lockfile refresh alone turned `import server`
into a `ModuleNotFoundError`. Adding or removing a tool means updating
`EXPECTED_TOOLS` there on purpose.

## Example Queries

- "Show me the last hour of assistant errors on pulse-kitchen"
- "What's the service status on pulse-bedroom?"
- "Run diagnostics on pulse-office"
- "Compare configs across all devices"
- "Search logs for 'Wyoming' on pulse-kitchen since yesterday"
