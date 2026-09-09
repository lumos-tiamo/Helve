"""The MCP endpoint.

One POST route rather than a transport implementation: `tools/call` here is a
plain request/response, and neither exposed tool streams. A host that needs SSE
gets the same JSON over the streamable-HTTP transport's non-streaming path.

Auth is the same bearer token as the rest of the API — an MCP host running on
this machine reads it from ``~/.helve/auth_token`` exactly as the shell does.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..mcp import handle_payload

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is a protocol error, not a crash
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}
        )

    response = handle_payload(payload)
    # A notification produces no body.  202 rather than 200-with-null is what
    # the spec asks for and what a strict host checks.
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)
