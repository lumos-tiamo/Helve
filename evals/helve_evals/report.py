"""Rendering suite results: a console table, a JSON record, a Markdown summary.

The console output is the one a person reads while iterating, so it leads with
what failed and why, not with a score.  The JSON is the one a later run diffs
against, so it holds every check rather than the summary.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .assertions import Dimension
from .scoring import SuiteResult, compare

_GREEN, _RED, _DIM, _BOLD, _RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _paint(text: str, colour: str, use_colour: bool) -> str:
    return f"{colour}{text}{_RESET}" if use_colour else text


def render_console(suite: SuiteResult, *, colour: bool = True, verbose: bool = False) -> str:
    lines: list[str] = []
    width = max((len(r.case_id) for r in suite.results), default=4)

    lines.append(_paint(f"model: {suite.model}", _BOLD, colour))
    lines.append("")

    for result in suite.results:
        mark = _paint("PASS", _GREEN, colour) if result.passed else _paint("FAIL", _RED, colour)
        trajectory = result.trajectory
        stats = (
            f"{len(trajectory.tool_calls):>2} tools  "
            f"{trajectory.total_tokens:>6} tok  "
            f"{trajectory.seconds:>5.1f}s"
        )
        lines.append(f"  {mark}  {result.case_id:<{width}}  {_paint(stats, _DIM, colour)}")
        if result.error:
            lines.append(f"        {_paint('! ' + result.error, _RED, colour)}")
        for check in result.checks:
            if check.passed and not verbose:
                continue
            bullet = "✓" if check.passed else "✗"
            colour_code = _GREEN if check.passed else _RED
            detail = f" — {check.detail}" if check.detail else ""
            lines.append(
                f"        {_paint(bullet, colour_code, colour)} "
                f"[{check.dimension.value}] {check.name}{detail}"
            )

    lines.append("")
    verdict = f"{suite.passed}/{suite.total} cases passed"
    lines.append(_paint(verdict, _GREEN if suite.passed == suite.total else _RED, colour))

    tallies = suite.by_dimension()
    for dimension in Dimension:
        tally = tallies.get(dimension)
        if tally is None or tally.total == 0:
            continue
        lines.append(
            f"  {dimension.value:<11} {tally.passed:>3}/{tally.total:<3} checks "
            f"({tally.rate:>5.0%})"
        )
    lines.append(
        _paint(
            f"  {suite.total_tool_calls} tool calls · {suite.total_tokens} tokens · "
            f"{suite.total_seconds:.1f}s total",
            _DIM,
            colour,
        )
    )
    return "\n".join(lines)


def to_dict(suite: SuiteResult) -> dict:
    return {
        "model": suite.model,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "cases": suite.total,
            "passed": suite.passed,
            "pass_rate": round(suite.pass_rate, 4),
            "tool_calls": suite.total_tool_calls,
            "tokens": suite.total_tokens,
            "seconds": round(suite.total_seconds, 2),
            "by_dimension": {
                dimension.value: {"passed": tally.passed, "total": tally.total}
                for dimension, tally in suite.by_dimension().items()
            },
        },
        "cases": [
            {
                "id": result.case_id,
                "passed": result.passed,
                "error": result.error,
                "tool_calls": list(result.trajectory.tool_names),
                "route": result.trajectory.route,
                "skills": list(result.trajectory.skills),
                "tokens": result.trajectory.total_tokens,
                "seconds": round(result.trajectory.seconds, 2),
                "checks": [
                    {**asdict(check), "dimension": check.dimension.value}
                    for check in result.checks
                ],
            }
            for result in suite.results
        ],
    }


def write_json(suite: SuiteResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(suite), indent=2, ensure_ascii=False), encoding="utf-8")


def render_matrix(suites: list[SuiteResult]) -> str:
    """One row per case, one column per model — the whole point of running more than one."""
    if not suites:
        return "no results"
    case_ids: list[str] = []
    for suite in suites:
        for result in suite.results:
            if result.case_id not in case_ids:
                case_ids.append(result.case_id)

    lookup = {
        (suite.model, result.case_id): result for suite in suites for result in suite.results
    }
    id_width = max(max((len(c) for c in case_ids), default=4), 4)
    columns = [max(len(suite.model), 6) for suite in suites]

    header = "| " + "case".ljust(id_width) + " | "
    header += " | ".join(suite.model.ljust(width) for suite, width in zip(suites, columns)) + " |"
    divider = "|" + "-" * (id_width + 2) + "|" + "|".join("-" * (w + 2) for w in columns) + "|"

    rows = [header, divider]
    for case_id in case_ids:
        cells = []
        for suite, width in zip(suites, columns):
            result = lookup.get((suite.model, case_id))
            cells.append(("—" if result is None else ("pass" if result.passed else "FAIL")).ljust(width))
        rows.append("| " + case_id.ljust(id_width) + " | " + " | ".join(cells) + " |")

    totals = []
    for suite, width in zip(suites, columns):
        totals.append(f"{suite.passed}/{suite.total}".ljust(width))
    rows.append("| " + "**total**".ljust(id_width) + " | " + " | ".join(totals) + " |")
    return "\n".join(rows)


def render_regressions(baseline: SuiteResult, candidate: SuiteResult) -> str:
    deltas = compare(baseline, candidate)
    if not deltas:
        return f"no case changed verdict between {baseline.model} and {candidate.model}"
    lines = [f"changes from {baseline.model} → {candidate.model}:"]
    for case_id, kind in deltas:
        lines.append(f"  {kind:<10} {case_id}")
    return "\n".join(lines)
