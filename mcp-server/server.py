#!/usr/bin/env python3
"""Pulse OS MCP server for device diagnostics and log reading."""

from __future__ import annotations

import logging
import sys

from config import load_config
from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
from ssh import PulseSSH

# All logging to stderr (stdout is reserved for JSON-RPC in STDIO transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pulse-mcp")

# Load configuration and initialize SSH pool
config = load_config()
ssh = PulseSSH(config.ssh)

# Create the FastMCP server
mcp = FastMCP("pulse-os")

# Register tools from each module — passing shared dependencies
from tools.config_tools import _register as _reg_config  # noqa: E402
from tools.diagnostics import _register as _reg_diag  # noqa: E402
from tools.logs import _register as _reg_logs  # noqa: E402
from tools.status import _register as _reg_status  # noqa: E402

_reg_status(mcp, ssh, config)
_reg_logs(mcp, ssh, config)
_reg_config(mcp, ssh, config)
_reg_diag(mcp, ssh, config)


def main() -> None:
    logger.info("Pulse MCP server starting with %d configured devices", len(config.devices))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
