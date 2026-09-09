"""Scenario evaluation harness for the Helve agent.

Scope, stated up front because it is the thing eval harnesses get wrong: this
measures what the agent *did to the world*, on real provider calls.  It does not
measure prose, and it is not a substitute for the mock-driven suites in
``engine/tests`` and ``server/tests`` -- those pin harness logic, this pins
capability.
"""

from .assertions import Check, Dimension, evaluate
from .case import Case, CaseError, load_suite, parse_case
from .scoring import CaseResult, SuiteResult, compare, score_case
from .trajectory import Trajectory, build_trajectory

__all__ = [
    "Case", "CaseError", "load_suite", "parse_case",
    "Check", "Dimension", "evaluate",
    "Trajectory", "build_trajectory",
    "CaseResult", "SuiteResult", "score_case", "compare",
]
