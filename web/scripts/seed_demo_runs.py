"""Seed the observability store with plausible runs, for developing the console.

Why this exists: the console's interesting views -- the waterfall, the prompt
composition, the diagnosis panel -- are all empty until an agent has actually
run, and a real run costs a provider call.  Iterating on layout at that price is
absurd, and shipping views nobody has seen with data in them is worse.

What it is *not*: an eval, a test, or evidence about the agent.  Every number
here is invented.  It writes through ``RunEventRecorder`` -- the same path the
engine uses -- rather than fabricating store files, so a change to the storage
format breaks this script instead of silently producing runs the real reader
cannot parse.

    uv run --project server python web/scripts/seed_demo_runs.py
    uv run --project server python web/scripts/seed_demo_runs.py --clear
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from datetime import datetime, timezone
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import AGENT_DIR  # noqa: E402
from engine.execution.events import EventType, ExecutionEvent  # noqa: E402
from engine.observability.recorder import RunEventRecorder  # noqa: E402
from engine.observability.summary_store import RunMetadata, RunSummaryStore  # noqa: E402
from engine.observability.trace_store import TraceStore  # noqa: E402

def resolve_agent_id() -> str:
    """The profile id the API filters by, read from the same store it reads.

    Hardcoding "smith" writes runs the API will never return: the observability
    routes scope every query to the *profile* id, which is generated per
    install.  Reading it here is the difference between a seeded console and an
    empty one that looks broken.
    """
    import sqlite3

    from common.config import PATHS

    database = PATHS.sqlite_path
    if not database.is_file():
        raise SystemExit(
            f"no database at {database} — start the server once so it creates a profile"
        )
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT id FROM agent_profiles LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("no agent profile yet — start the server once, then re-run")
    return str(row[0])


AGENT_ID = resolve_agent_id()

# Shapes worth having on screen: a clean run, a slow one, one that needed
# several approvals, one that failed, one that backtracked.  A console that has
# only ever been seen with happy data hides its own worst layouts.
SCENARIOS: list[dict] = [
    {
        "name": "read-and-answer",
        "identity": "smith",
        "route": "chat",
        "outcome": "completed",
        "tools": [("read_file", 120, True, False), ("grep", 240, True, False)],
    },
    {
        "name": "write-with-approval",
        "identity": "smith",
        "route": "git",
        "outcome": "completed",
        "tools": [
            ("read_file", 90, True, False),
            ("write_file", 3400, True, True),
            ("shell", 1800, True, True),
        ],
    },
    {
        "name": "slow-crawl",
        "identity": "smith",
        "route": "chat",
        "outcome": "completed",
        "tools": [("web_search", 2600, True, False), ("web_crawl", 11400, True, False)],
    },
    {
        "name": "failed-edit",
        "identity": "coding",
        "route": "code-review",
        "outcome": "failed",
        "reason": "edit_file could not locate the target region",
        "tools": [
            ("read_file", 110, True, False),
            ("edit_file", 700, False, True),
        ],
    },
    {
        "name": "backtracked-plan",
        "identity": "coding",
        "route": "tdd-development",
        "outcome": "incomplete",
        "reason": "gate rejected the evidence twice",
        "tools": [
            ("read_file", 130, True, False),
            ("write_file", 900, True, True),
            ("shell", 5200, True, True),
            ("edit_file", 640, True, True),
        ],
        "backtracks": 2,
    },
]

# A believable prompt manifest, with durable memory large enough that the
# retrieval panel has something to say.
PROMPT_LAYERS = [
    {"id": "role", "token_estimate": 420},
    {"id": "toolbox", "token_estimate": 1180},
    {"id": "skills", "token_estimate": 760},
    {"id": "output_style", "token_estimate": 240},
    {"id": "memory_governance", "token_estimate": 130},
    {"id": "durable_context", "token_estimate": 640},
    {"id": "runtime_context", "token_estimate": 310},
]


def seed_one(scenario: dict, rng: random.Random) -> str:
    run_id = f"demo-{scenario['name']}-{rng.randrange(16**6):06x}"
    trace_store = TraceStore(AGENT_DIR)
    summary_store = RunSummaryStore(AGENT_DIR)
    # The sink closes over metadata exactly the way engine.observability.runtime
    # wires it; the summary the recorder projects carries no identity of its own.
    metadata = RunMetadata(
        run_id=run_id,
        agent_id=AGENT_ID,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        session_id=f"demo-session-{scenario['name']}",
        identity_id=scenario["identity"],
        route_id=scenario["route"],
        working_dir="/demo/workspace",
    )
    recorder = RunEventRecorder(
        run_id,
        trace_store=trace_store,
        summary_sinks=(lambda summary: summary_store.save(metadata, summary),),
    )

    def emit(event_type: EventType, **data: object) -> None:
        recorder.record(ExecutionEvent(type=event_type, data=data))
        # Real timestamps come from the recorder, so the waterfall needs the
        # calls to actually be separated in time.  A millisecond per 40ms of
        # claimed duration keeps the whole script to a few seconds.
        time.sleep(0.001)

    emit(EventType.RUN_STARTED, agent_id=AGENT_ID, run_id=run_id)
    emit(
        EventType.ROUTE_DECIDED,
        route=scenario["route"],
        identity=scenario["identity"],
    )
    recorder.append_prompt_manifest({"layers": PROMPT_LAYERS})

    kept, total = 9, 34
    emit(
        EventType.CONTEXT_USAGE,
        memory_retrieval={
            "strategy": "lexical",
            "reason": f"kept {kept}/{total} bullets",
            "chunks_total": total,
            "chunks_kept": kept,
            "saved_ratio": 0.58,
        },
    )

    input_tokens = 0
    for name, duration_ms, ok, needs_approval in scenario["tools"]:
        emit(EventType.TOOL_CALL_START, name=name)
        if needs_approval:
            emit(EventType.AWAITING_INPUT, kind="approval", tool=name)
        time.sleep(duration_ms / 4_000)
        emit(
            EventType.TOOL_CALL_RESULT,
            name=name,
            ok=ok,
            # The projection counts approvals off this flag, not off
            # awaiting_input — matching engine.observability.projections.
            approval_required=needs_approval,
            **({} if ok else {"error": "target region not found"}),
        )
        input_tokens += rng.randrange(400, 1600)

    for _ in range(scenario.get("backtracks", 0)):
        emit(EventType.BACKTRACK, reason="gate rejected the evidence")

    output_tokens = rng.randrange(200, 900)
    emit(
        EventType.TOKEN_USAGE,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        # engine/llm/usage.py always fills this in, falling back to the sum,
        # and the API reads it directly rather than recomputing.
        total_tokens=input_tokens + output_tokens,
    )

    if scenario["outcome"] == "failed":
        emit(EventType.FAILED, error=scenario.get("reason", "run failed"))
    else:
        emit(EventType.DONE, outcome=scenario["outcome"])
    emit(
        EventType.RUN_FINISHED,
        # The projection reads "status"; "outcome" would be silently ignored
        # and every run would list as unknown.
        status=scenario["outcome"],
        reason=scenario.get("reason"),
        agent_id=AGENT_ID,
        identity_id=scenario["identity"],
        route_id=scenario["route"],
    )
    try:
        trace_store.seal(run_id)
    except Exception as exc:  # noqa: BLE001 - sealing is not what this script tests
        print(f"  (could not seal {run_id}: {exc})")
    return run_id


def clear() -> None:
    """Remove seeded runs.  Only touches ids this script creates."""
    removed = 0
    for directory in (AGENT_DIR / "runs", AGENT_DIR / "traces"):
        if not directory.is_dir():
            continue
        for path in directory.glob("demo-*"):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
    print(f"removed {removed} seeded artefacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="remove seeded runs and exit")
    parser.add_argument("--seed", type=int, default=7, help="rng seed, for repeatable ids")
    args = parser.parse_args(argv)

    if args.clear:
        clear()
        return 0

    rng = random.Random(args.seed)
    print(f"writing into {AGENT_DIR}")
    for scenario in SCENARIOS:
        run_id = seed_one(scenario, rng)
        print(f"  {scenario['outcome']:<10} {run_id}")
    print("\nOpen the console at http://127.0.0.1:8000/console")
    print("Remove them again with --clear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
