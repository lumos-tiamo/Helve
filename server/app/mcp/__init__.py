"""Helve as an MCP server: the other side of engine/mcp's client.

Exposes what this install *knows* — compiled project memory and available
skills — to any MCP host. Mutating tools are deliberately absent; see
``exposure`` for why that is a design decision and not a first-version gap.
"""

from .exposure import EXPOSED_TOOLS, TOOLS_BY_NAME, ExposedTool, McpToolError
from .server import SERVER_INFO, handle_message, handle_payload

__all__ = [
    "EXPOSED_TOOLS", "TOOLS_BY_NAME", "ExposedTool", "McpToolError",
    "SERVER_INFO", "handle_message", "handle_payload",
]
