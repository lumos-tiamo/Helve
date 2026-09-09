"""Aggregating checks into results a human can act on.

A single percentage hides the thing you need to know.  "8/10 passed" is the
same number whether the two failures were budget overruns or a write that
skipped approval, and those demand very different responses.  So a result keeps
its per-dimension breakdown all the way to the report, and the headline number
is deliberately the strict one: a case passes only when nothing failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .assertions import Check, Dimension
from .case import Case
from .trajectory import Trajectory


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    model: str
    checks: tuple[Check, ...]
    trajectory: Trajectory
    error: str | None = None

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks) and not self.error

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def failed_dimensions(self) -> tuple[Dimension, ...]:
        seen: list[Dimension] = []
        for check in self.failures:
            if check.dimension not in seen:
                seen.append(check.dimension)
        return tuple(seen)


@dataclass(slots=True)
class DimensionTally:
    passed: int = 0
    total: int = 0

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0


@dataclass(slots=True)
class SuiteResult:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def total_tokens(self) -> int:
        return sum(result.trajectory.total_tokens for result in self.results)

    @property
    def total_seconds(self) -> float:
        return sum(result.trajectory.seconds for result in self.results)

    @property
    def total_tool_calls(self) -> int:
        return sum(len(result.trajectory.tool_calls) for result in self.results)

    def by_dimension(self) -> dict[Dimension, DimensionTally]:
        """Check-level tallies, so one bad case cannot hide behind nine good ones."""
        tallies: dict[Dimension, DimensionTally] = {}
        for result in self.results:
            for check in result.checks:
                tally = tallies.setdefault(check.dimension, DimensionTally())
                tally.total += 1
                tally.passed += int(check.passed)
        return tallies


def score_case(
    case: Case, checks: Iterable[Check], trajectory: Trajectory, model: str, error: str | None
) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        model=model,
        checks=tuple(checks),
        trajectory=trajectory,
        error=error,
    )


def compare(baseline: SuiteResult, candidate: SuiteResult) -> list[tuple[str, str]]:
    """Case-level deltas between two models or two runs.

    Only changes are returned.  A regression list that also prints everything
    that stayed the same is a list nobody reads.
    """
    before = {result.case_id: result.passed for result in baseline.results}
    after = {result.case_id: result.passed for result in candidate.results}
    deltas: list[tuple[str, str]] = []
    for case_id in sorted(set(before) | set(after)):
        was, now = before.get(case_id), after.get(case_id)
        if was == now:
            continue
        if was is None:
            deltas.append((case_id, "added"))
        elif now is None:
            deltas.append((case_id, "removed"))
        else:
            deltas.append((case_id, "fixed" if now else "regressed"))
    return deltas
