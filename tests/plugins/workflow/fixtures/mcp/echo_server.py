from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


pid_file = os.environ.get("WORKFLOW_MCP_PID_FILE")
if pid_file:
    Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")

server = FastMCP("workflow-echo")


@server.tool()
def echo(text: str) -> str:
    """Return the supplied text."""
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
