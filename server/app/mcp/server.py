"""JSON-RPC 2.0 dispatch for Helve's MCP server endpoint.

Deliberately small: `initialize`, `tools/list`, `tools/call`, and the two
notifications a host sends during handshake.  Anything else gets a proper
method-not-found rather than a 500, because a host probing for optional
capabilities is normal traffic, not an error.

Protocol version is taken from ``engine.mcp.client`` so the two directions
cannot drift apart — this runtime speaking one version as a client and another
as a server would be a bug nobody would look for.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.mcp.client import PROTOCOL_VERSION

from .exposure import TOOLS_BY_NAME, EXPOSED_TOOLS, McpToolError

logger = logging.getLogger(__name__)

SERVER_INFO = {"name": "helve", "version": "0.2.0"}

# JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _initialize() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        # Only tools.  Declaring resources or prompts we do not serve would make
        # a host issue calls that can only fail.
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": (
            "Helve exposes what it knows, not what it can do: its compiled project "
            "memory and the skills it can run. Mutating tools stay behind the "
            "terminal, where a human approves them."
        ),
    }


def _tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise McpToolError("params.name must be a string")
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise McpToolError(f"unknown tool {name!r}")

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise McpToolError("params.arguments must be an object")

    payload = tool.handler(arguments)
    # structuredContent is what a modern host reads; the text block is the
    # fallback for one that does not, and omitting it makes the call look empty
    # rather than successful.
    import json

    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload,
        "isError": False,
    }


def handle_message(message: Any) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC message.  Returns None for a notification."""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "message must be a JSON object")
    if message.get("jsonrpc") != "2.0":
        return _error(message.get("id"), INVALID_REQUEST, "jsonrpc must be '2.0'")

    method = message.get("method")
    if not isinstance(method, str):
        return _error(message.get("id"), INVALID_REQUEST, "method must be a string")

    request_id = message.get("id")
    params = message.get("params")
    if params is not None and not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "params must be an object")
    params = params or {}

    # Notifications carry no id and must produce no response body at all.
    if request_id is None:
        if method.startswith("notifications/"):
            return None
        return None

    try:
        if method == "initialize":
            return _result(request_id, _initialize())
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [tool.to_wire() for tool in EXPOSED_TOOLS]})
        if method == "tools/call":
            return _result(request_id, _tools_call(params))
        return _error(request_id, METHOD_NOT_FOUND, f"unsupported method {method!r}")
    except McpToolError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool bug must not take the endpoint down
        logger.exception("mcp: %s failed", method)
        return _error(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")


def handle_payload(payload: Any) -> Any:
    """Handle a single message or a batch, per JSON-RPC 2.0."""
    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "batch must not be empty")
        responses = [handle_message(item) for item in payload]
        kept = [response for response in responses if response is not None]
        # An all-notification batch gets no body, same as a single notification.
        return kept or None
    return handle_message(payload)
