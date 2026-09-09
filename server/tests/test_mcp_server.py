"""Helve as an MCP server.

Two things are worth pinning here, and only one of them is the protocol.

The protocol half is ordinary: a host handshakes, lists tools, calls one, and a
notification must produce no body.

The half that matters is the **boundary**. Every mutating tool in this runtime
pauses for a human, and an MCP caller has no human. So the test below asserts
that no exposed tool is a write tool — asked of the registry rather than of a
hardcoded name list, so adding a tool to the exposure surface without thinking
about it fails here instead of shipping.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mcp import EXPOSED_TOOLS, McpToolError, handle_payload  # noqa: E402
from app.mcp.exposure import _memory_search  # noqa: E402


def _request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    payload: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return handle_payload(payload)


# ── the boundary ───────────────────────────────────────────────────────────

def test_no_exposed_tool_can_mutate_anything():
    """The safety property this endpoint rests on.

    A write tool reachable over MCP would either block forever on an approval
    nobody will answer, or run without one — and the second is the guard
    becoming decoration.
    """
    from common.config import PATHS
    from engine.tool.registry import ToolRegistry

    registry = ToolRegistry()
    registry.load_builtin_providers(PATHS.builtin_tools_dir)

    for tool in EXPOSED_TOOLS:
        definition = registry.get(tool.name)
        # The exposed tools are purpose-built rather than registry tools, so a
        # name that *does* resolve means someone wired a runtime tool straight
        # through — exactly the mistake worth catching.
        if definition is None:
            continue
        assert not definition.is_write_tool, (
            f"{tool.name} is a write tool and must not be reachable over MCP"
        )
        assert definition.approval_policy == "never", (
            f"{tool.name} needs approval, and an MCP caller has nobody to ask"
        )


def test_the_exposed_surface_is_declared_not_incidental():
    """A tool appears here only by being written into EXPOSED_TOOLS."""
    assert {tool.name for tool in EXPOSED_TOOLS} == {"memory_search", "skills_list"}


# ── protocol ───────────────────────────────────────────────────────────────

def test_initialize_reports_the_same_protocol_version_the_client_speaks():
    """One runtime speaking two versions in two directions is a bug nobody looks for."""
    from engine.mcp.client import PROTOCOL_VERSION

    result = _request("initialize", {})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "helve"
    # Declaring a capability we do not serve makes a host issue calls that can
    # only fail.
    assert set(result["capabilities"]) == {"tools"}


def test_tools_list_returns_usable_schemas():
    tools = _request("tools/list")["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"memory_search", "skills_list"}
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_a_tool_call_returns_both_structured_and_text_content():
    """A host that reads only the text block must see the same answer.

    Asserted as a contract between the two representations rather than against
    a skill count: the suite runs against an isolated data root, so how many
    skills exist here is an accident of the fixture, not the thing under test.
    """
    import json

    result = _request("tools/call", {"name": "skills_list", "arguments": {}})["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert set(structured) == {"skills", "count"}
    assert structured["count"] == len(structured["skills"])

    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == structured


def test_a_notification_produces_no_response_body():
    assert handle_payload({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_a_batch_drops_notifications_and_keeps_answers():
    responses = handle_payload(
        [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        ]
    )
    assert len(responses) == 1
    assert responses[0]["id"] == 7


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"jsonrpc": "1.0", "id": 1, "method": "ping"}, -32600),
        ({"jsonrpc": "2.0", "id": 1, "method": 42}, -32600),
        ({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "rm_rf"}}, -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}, -32601),
    ],
)
def test_malformed_and_unknown_requests_get_protocol_errors(payload, code):
    """A host probing for optional capabilities is normal traffic, not a 500."""
    assert handle_payload(payload)["error"]["code"] == code


def test_a_failing_tool_becomes_an_error_response_not_a_crash(monkeypatch):
    def explode(_arguments):
        raise RuntimeError("disk went away")

    monkeypatch.setitem(
        __import__("app.mcp.server", fromlist=["TOOLS_BY_NAME"]).TOOLS_BY_NAME,
        "skills_list",
        type(EXPOSED_TOOLS[1])(
            name="skills_list",
            description="x",
            input_schema={"type": "object"},
            handler=explode,
        ),
    )
    error = _request("tools/call", {"name": "skills_list", "arguments": {}})["error"]
    assert error["code"] == -32603
    assert "disk went away" in error["message"]


# ── memory_search ──────────────────────────────────────────────────────────

def test_memory_search_rejects_an_empty_query():
    with pytest.raises(McpToolError):
        _memory_search({"query": "   "})


def test_memory_search_reports_an_install_with_no_memory_rather_than_failing():
    """A fresh install is a normal state, not an error the caller should retry."""
    from common import config as common_config

    class _Paths:
        agent_dir = Path("/nonexistent-helve-agent-dir")

    original = common_config.PATHS
    common_config.PATHS = _Paths()  # type: ignore[assignment]
    try:
        result = _memory_search({"query": "anything"})
    finally:
        common_config.PATHS = original
    assert result["bullets"] == []
    assert "no project memory" in result["note"]


def test_memory_search_caps_a_greedy_limit(tmp_path, monkeypatch):
    """Asking for 500 entries is asking for the whole document by another name."""
    from common import config as common_config

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    bullets = "\n".join(f"- **topic-{i}**: fact number {i} about sqlite" for i in range(60))
    (memory_dir / "durable.md").write_text(
        f"# Project Memory\n\n## Decisions\n{bullets}\n", encoding="utf-8"
    )

    class _Paths:
        agent_dir = tmp_path

    monkeypatch.setattr(common_config, "PATHS", _Paths())
    result = _memory_search({"query": "sqlite", "limit": 500})
    assert result["returned"] <= 25
    assert result["total_bullets"] == 60


# ── the HTTP surface ───────────────────────────────────────────────────────

# TestClient is built *without* its context manager on purpose: entering it
# runs the real lifespan — database, scheduler, provider clients — which the
# other suites monkeypatch away wholesale.  None of that is what these two
# assert.  Routing and dependencies are wired at import time, so a plain client
# exercises the real route and the real auth dependency without booting the app.

def test_the_endpoint_requires_the_same_bearer_token_as_the_rest_of_the_api():
    from app.main import app

    response = TestClient(app).post(
        "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
    )
    assert response.status_code == 401


def test_malformed_json_is_a_protocol_error_not_a_500():
    from app.infrastructure.auth import get_local_token
    from app.main import app

    response = TestClient(app).post(
        "/api/mcp",
        content=b"{not json",
        headers={
            "Authorization": f"Bearer {get_local_token()}",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32700
