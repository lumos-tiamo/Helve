"""Executing one case against the real engine.

Two lessons are baked in here, both learned the expensive way by
``server/tests/test_e2e_smoke.py`` and repeated because forgetting either one
produces a suite that looks green and measures nothing:

* **Something has to answer the approval prompt.**  Write tools park on
  ``ApprovalBroker.wait()`` until a human clicks.  With no UI and no stand-in,
  every mutating case burns the full 300-second timeout.
* **A run that never reached DONE has to fail loudly.**  Its tool list is empty,
  which is exactly what a correct "do nothing" run produces.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .case import Case
from .trajectory import Trajectory, build_trajectory

# The eval workspace is a throwaway directory, but the approval stand-in below
# says yes to everything, so a case must never be pointed at a real project.
APPROVAL_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class RunOutcome:
    trajectory: Trajectory
    workspace: Path
    error: str | None = None


class _AutoApprover:
    """Stands in for the human at the keyboard.

    It records what it approved rather than only unblocking, because "the tool
    ran" and "the tool asked first" are different facts and the safety checks
    need the second one.
    """

    def __init__(self) -> None:
        self.approved: list[str] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _AutoApprover:
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown
                pass

    async def _loop(self) -> None:
        from common.config import AGENT_DIR
        from engine.execution import RunStateStore, RunStatus
        from engine.safety.approval import APPROVAL_BROKER

        store = RunStateStore(AGENT_DIR)
        while not self._stop.is_set():
            for path in store.root.glob("*.json"):
                try:
                    state = store.get(path.stem)
                except Exception:  # noqa: BLE001 - a half-written state must not stop the approver
                    continue
                if (
                    state is not None
                    and state.status is RunStatus.WAITING_APPROVAL
                    and state.approval_id
                ):
                    self.approved.append(state.approval_id)
                    APPROVAL_BROKER.resolve(state.run_id, state.approval_id, True)
            await asyncio.sleep(APPROVAL_POLL_SECONDS)


def materialize_workspace(case: Case, root: Path) -> Path:
    """Create the case's starting world under a fresh directory."""
    workspace = root / case.id
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, content in case.given_files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return workspace


async def run_case(case: Case, workspace: Path, *, timeout: float = 300.0) -> RunOutcome:
    """Execute one case and return what the run actually did."""
    from engine.execution import (
        EngineRequest,
        run_stream_with_runtime,
    )
    from app.services.engine_runtime import build_engine_runtime

    runtime, services = build_engine_runtime(
        case.identity,
        case.identity.capitalize(),
        session_id=f"eval-{case.id}-{uuid.uuid4().hex[:6]}",
    )
    collected: list[tuple[str, dict]] = []
    started = time.monotonic()
    error: str | None = None

    async with _AutoApprover() as approver:
        stream = run_stream_with_runtime(
            EngineRequest(message=case.prompt, working_dir=str(workspace)),
            runtime,
            services,
        )
        try:
            async with asyncio.timeout(timeout):
                async for event in stream.stream_events():
                    collected.append((event.type.value, dict(event.data or {})))
        except TimeoutError:
            error = f"the run exceeded the {timeout:g}s harness timeout"
        except Exception as exc:  # noqa: BLE001 - a crashed run is a result, not a harness bug
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await stream.aclose()

    trajectory = build_trajectory(collected, time.monotonic() - started)
    # The broker only sees approval ids, so pair its count with the tool calls
    # the stream paused on; a mismatch means a write slipped past the prompt.
    if approver.approved and not trajectory.approvals_requested:
        trajectory.approvals_requested = [
            call.name for call in trajectory.tool_calls if call.approved
        ]
    if error and trajectory.failed_reason is None:
        trajectory.failed_reason = error
    return RunOutcome(trajectory=trajectory, workspace=workspace, error=error)
