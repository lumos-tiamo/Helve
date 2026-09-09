"""What one run actually did, reduced to the facts a case can assert on.

Everything here comes from the execution event stream, which is the same stream
the shell renders.  Nothing is scraped from the model's prose: a run that
announces "I created the file" and a run that created the file are different
runs, and only one of them shows up here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    # The provider's call id.  Results are paired on this, never on the name:
    # a gated call emits an intermediate result that carries only the id, and
    # matching on name would attach it to the wrong call — or to no call.
    call_id: str = ""
    ok: bool | None = None
    approval_requested: bool = False


@dataclass(slots=True)
class Trajectory:
    """The observable shape of one run."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    route: str | None = None
    skills: list[str] = field(default_factory=list)
    gate_results: list[tuple[str, bool]] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    approvals_requested: list[str] = field(default_factory=list)
    # Reached DONE.  A statement of fact, not a verdict: the engine emits
    # ``failed`` and then ``done``, so this is true for a failed run too.
    # ``assertions`` is where the two are combined into success.
    completed: bool = False
    failed_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    # The tail of event types, kept for the failure message when a run does not
    # reach DONE.  A run that dies mid-stream produces empty tool calls, which
    # looks identical to a run that correctly did nothing.
    event_tail: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def build_trajectory(events: Iterable[tuple[str, dict[str, Any]]], seconds: float) -> Trajectory:
    """Fold an event stream into a Trajectory.

    Event *types* arrive as plain strings so this stays importable without the
    engine -- the harness's own unit tests build streams by hand.
    """
    trajectory = Trajectory(seconds=seconds)
    types: list[str] = []

    for event_type, data in events:
        types.append(event_type)
        payload = data or {}

        if event_type == "tool_call_start":
            trajectory.tool_calls.append(
                ToolCall(
                    name=str(payload.get("name", "")),
                    call_id=str(payload.get("id", "")),
                )
            )
        elif event_type == "tool_call_result":
            _attach_result(trajectory, payload)
        elif event_type == "route_decided":
            trajectory.route = _first_str(payload, ("route", "route_id", "id"))
        elif event_type == "skill_start":
            name = _first_str(payload, ("skill", "name", "skill_name"))
            if name:
                trajectory.skills.append(name)
        elif event_type == "gate_result":
            gate = _first_str(payload, ("gate", "name", "node")) or "?"
            trajectory.gate_results.append((gate, bool(payload.get("passed", payload.get("ok")))))
        elif event_type == "blocked":
            trajectory.blocked_reasons.append(
                _first_str(payload, ("reason", "message", "detail")) or "blocked"
            )
        elif event_type == "awaiting_input":
            # A secondary signal only.  The engine marks tool approvals on the
            # result (see _attach_result); awaiting_input covers the cases where
            # a run pauses for the user without a tool result to hang it on.
            pending = trajectory.tool_calls[-1].name if trajectory.tool_calls else ""
            if pending and pending not in trajectory.approvals_requested:
                trajectory.approvals_requested.append(pending)
        elif event_type == "token_usage":
            trajectory.input_tokens += _int(payload, ("input_tokens", "prompt_tokens", "input"))
            trajectory.output_tokens += _int(payload, ("output_tokens", "completion_tokens", "output"))
        elif event_type == "failed":
            trajectory.failed_reason = (
                _first_str(payload, ("error", "reason", "message")) or "failed"
            )
        elif event_type == "done":
            trajectory.completed = True

    trajectory.event_tail = types[-6:]
    return trajectory


def _attach_result(trajectory: Trajectory, payload: dict[str, Any]) -> None:
    """Apply one result to the call it belongs to.

    One tool call can emit several results, and only the last is the outcome:

    * ``preflight: true``  — the fact gate challenged the call
    * ``approval_required: true`` — it is waiting for a human
    * otherwise            — the call actually finished

    Treating the first of those as the outcome is how a gated write gets
    recorded as "ran without pausing", which is exactly backwards.
    """
    index = _find_call(trajectory, payload)
    if index is None:
        return
    call = trajectory.tool_calls[index]

    if payload.get("approval_required"):
        trajectory.tool_calls[index] = ToolCall(
            name=call.name, call_id=call.call_id, ok=call.ok, approval_requested=True
        )
        if call.name and call.name not in trajectory.approvals_requested:
            trajectory.approvals_requested.append(call.name)
        return

    if payload.get("preflight"):
        # A gate challenge is not an outcome; the call is still open.
        return

    ok = payload.get("ok")
    if ok is None:
        ok = not payload.get("error")
    trajectory.tool_calls[index] = ToolCall(
        name=call.name,
        call_id=call.call_id,
        ok=bool(ok),
        approval_requested=call.approval_requested,
    )


def _find_call(trajectory: Trajectory, payload: dict[str, Any]) -> int | None:
    """Locate the call a result belongs to: by id, then by name, then the last open one."""
    call_id = str(payload.get("id", ""))
    if call_id:
        for index in range(len(trajectory.tool_calls) - 1, -1, -1):
            if trajectory.tool_calls[index].call_id == call_id:
                return index
    name = str(payload.get("name", ""))
    for index in range(len(trajectory.tool_calls) - 1, -1, -1):
        call = trajectory.tool_calls[index]
        if call.ok is None and (not name or call.name == name):
            return index
    return None


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0
