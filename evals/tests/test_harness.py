"""The harness's own regression suite.

An eval harness is measurement equipment, and unverified measurement equipment
produces confident numbers about nothing.  Everything here runs offline in
milliseconds: no provider, no engine, no cost.  What it pins is that a case
means what it says, that a trajectory is read off the event stream correctly,
and — most importantly — that a broken run cannot score as a pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helve_evals.assertions import Dimension, evaluate  # noqa: E402
from helve_evals.case import CaseError, load_suite, parse_case  # noqa: E402
from helve_evals.report import render_matrix, to_dict  # noqa: E402
from helve_evals.scoring import SuiteResult, compare, score_case  # noqa: E402
from helve_evals.trajectory import build_trajectory  # noqa: E402


def _done(*events):
    return [*events, ("done", {})]


def _case(**overrides):
    document = {
        "id": "demo",
        "prompt": "do the thing",
        "expect": {"files": {"a.txt": {"contains": ["hello"]}}},
    }
    document.update(overrides)
    return parse_case(document)


# ── case parsing ───────────────────────────────────────────────────────────

def test_a_case_round_trips_its_declared_expectations():
    case = _case(
        tags=["core"],
        given={"files": {"b.txt": "seed\n"}},
        expect={
            "files": {"a.txt": {"contains": ["hello"], "absent": ["bye"]}},
            "tools": {"must_call": "write_file", "must_not_call": ["shell"], "max_calls": 3},
        },
        budget={"max_tool_calls": 4, "max_seconds": 30},
    )
    assert case.given_files == {"b.txt": "seed\n"}
    assert case.expect_files["a.txt"].contains == ("hello",)
    assert case.expect_files["a.txt"].absent == ("bye",)
    # A bare string is accepted where a list is natural; silently dropping it
    # would make a case assert less than it reads as asserting.
    assert case.expect_tools.must_call == ("write_file",)
    assert case.budget.max_tool_calls == 4


@pytest.mark.parametrize(
    "document, fragment",
    [
        ({"prompt": "x"}, "id"),
        ({"id": "a", "prompt": ""}, "prompt"),
        ({"id": "a", "prompt": "x", "nope": 1}, "unknown top-level"),
        ({"id": "a", "prompt": "x", "expect": {"tools": {"max_calls": -1}}}, "max_calls"),
        ({"id": "a", "prompt": "x", "budget": {"max_seconds": 0}}, "max_seconds"),
        ({"id": "a", "prompt": "x", "expect": {"whoops": 1}}, "unknown keys"),
    ],
)
def test_a_malformed_case_fails_at_load_time_not_run_time(document, fragment):
    """A typo must not become a check that silently never runs."""
    with pytest.raises(CaseError) as excinfo:
        parse_case(document)
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "nested/../../out.txt"])
def test_a_case_cannot_address_paths_outside_its_workspace(bad):
    """Case files are content.  Content does not get to pick absolute paths."""
    with pytest.raises(CaseError):
        parse_case({"id": "a", "prompt": "x", "given": {"files": {bad: ""}}})
    with pytest.raises(CaseError):
        parse_case({"id": "a", "prompt": "x", "expect": {"files": {bad: True}}})


def test_duplicate_case_ids_are_rejected(tmp_path):
    """Two cases with one id silently shrink a suite and overwrite a report row."""
    (tmp_path / "a.yaml").write_text("id: same\nprompt: one\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("id: same\nprompt: two\n", encoding="utf-8")
    with pytest.raises(CaseError, match="duplicate case id"):
        load_suite(tmp_path)


def test_the_shipped_suite_parses():
    """The suite is only useful if it loads; a typo here is a silent hole."""
    cases = load_suite(Path(__file__).resolve().parents[1] / "suites")
    assert len(cases) >= 8
    assert len({case.id for case in cases}) == len(cases)


# ── trajectory ─────────────────────────────────────────────────────────────

def test_the_trajectory_is_read_off_the_event_stream():
    trajectory = build_trajectory(
        _done(
            ("route_decided", {"route": "git"}),
            ("tool_call_start", {"name": "read_file"}),
            ("tool_call_result", {"name": "read_file", "ok": True}),
            ("skill_start", {"skill": "coding-implementation"}),
            ("token_usage", {"input_tokens": 100, "output_tokens": 20}),
        ),
        seconds=1.5,
    )
    assert trajectory.tool_names == ("read_file",)
    assert trajectory.route == "git"
    assert trajectory.skills == ["coding-implementation"]
    assert trajectory.total_tokens == 120
    assert trajectory.completed is True


def test_token_usage_accumulates_across_turns():
    """A multi-turn run reports usage per turn; taking the last one under-counts."""
    trajectory = build_trajectory(
        _done(
            ("token_usage", {"input_tokens": 100, "output_tokens": 10}),
            ("token_usage", {"input_tokens": 200, "output_tokens": 30}),
        ),
        seconds=0.1,
    )
    assert trajectory.total_tokens == 340


def test_an_approval_pause_is_attributed_to_the_open_tool_call():
    trajectory = build_trajectory(
        _done(
            ("tool_call_start", {"name": "write_file"}),
            ("awaiting_input", {"kind": "approval"}),
            ("tool_call_result", {"name": "write_file", "ok": True}),
        ),
        seconds=0.2,
    )
    assert trajectory.approvals_requested == ["write_file"]


# ── the assertion that matters most ────────────────────────────────────────

def test_a_run_that_never_finished_fails_even_when_the_files_are_right(tmp_path):
    """The failure mode this whole harness exists to avoid.

    A crashed run makes no tool calls, so a 'did not call write_file' check
    passes; if the file happens to exist from the setup, the task checks pass
    too.  Completion is therefore checked first and short-circuits everything.
    """
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    case = _case()
    trajectory = build_trajectory([("tool_call_start", {"name": "read_file"})], seconds=0.4)

    checks = evaluate(case, trajectory, tmp_path)

    assert [check.dimension for check in checks] == [Dimension.COMPLETION]
    assert checks[0].passed is False
    assert not any(check.dimension is Dimension.TASK for check in checks)


def test_a_finished_run_with_the_right_world_passes(tmp_path):
    (tmp_path / "a.txt").write_text("hello there", encoding="utf-8")
    case = _case(expect={
        "files": {"a.txt": {"contains": ["hello"]}},
        "tools": {"must_call": ["write_file"]},
    })
    trajectory = build_trajectory(
        _done(
            ("tool_call_start", {"name": "write_file"}),
            ("awaiting_input", {}),
            ("tool_call_result", {"name": "write_file", "ok": True}),
        ),
        seconds=1.0,
    )
    checks = evaluate(case, trajectory, tmp_path)
    assert all(check.passed for check in checks), [c for c in checks if not c.passed]


def test_a_write_that_skipped_approval_fails_on_safety(tmp_path):
    """Inferred, not declared: a new write path is covered the moment it is used."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    trajectory = build_trajectory(
        _done(("tool_call_start", {"name": "write_file"}),
              ("tool_call_result", {"name": "write_file", "ok": True})),
        seconds=1.0,
    )
    checks = evaluate(_case(), trajectory, tmp_path)
    safety = [check for check in checks if check.dimension is Dimension.SAFETY]
    assert safety and not safety[0].passed


def test_a_forbidden_tool_fails_the_trajectory_dimension(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    case = _case(expect={
        "files": {"a.txt": {"contains": ["hello"]}},
        "tools": {"must_not_call": ["shell"]},
    })
    trajectory = build_trajectory(
        _done(("tool_call_start", {"name": "shell"}),
              ("awaiting_input", {}),
              ("tool_call_result", {"name": "shell", "ok": True})),
        seconds=1.0,
    )
    failures = [c for c in evaluate(case, trajectory, tmp_path) if not c.passed]
    assert any(c.dimension is Dimension.TRAJECTORY for c in failures)


def test_an_unreported_token_count_does_not_silently_satisfy_a_budget(tmp_path):
    """A provider that reports nothing would otherwise make every budget pass."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    case = _case(budget={"max_total_tokens": 100})
    trajectory = build_trajectory(_done(), seconds=0.1)
    budget = [c for c in evaluate(case, trajectory, tmp_path) if c.dimension is Dimension.BUDGET]
    assert budget and not budget[0].passed
    assert "no token usage" in budget[0].detail


def test_an_expected_block_passes_without_reaching_done(tmp_path):
    case = _case(expect={"blocked": True})
    trajectory = build_trajectory([("blocked", {"reason": "outside the workspace"})], seconds=0.1)
    checks = evaluate(case, trajectory, tmp_path)
    assert checks[0].passed is True


def test_a_file_expected_absent_fails_when_it_appears(tmp_path):
    (tmp_path / "leak.txt").write_text("oops", encoding="utf-8")
    case = _case(expect={"files": {"leak.txt": False}})
    checks = evaluate(case, build_trajectory(_done(), seconds=0.1), tmp_path)
    assert not checks[1].passed


# ── scoring and reporting ──────────────────────────────────────────────────

def test_a_case_passes_only_when_every_check_passes(tmp_path):
    (tmp_path / "a.txt").write_text("nope", encoding="utf-8")
    case = _case()
    trajectory = build_trajectory(_done(), seconds=0.1)
    result = score_case(case, evaluate(case, trajectory, tmp_path), trajectory, "m", None)
    assert result.passed is False
    assert Dimension.TASK in result.failed_dimensions


def test_comparison_reports_only_what_changed():
    def suite(model, verdicts):
        result = SuiteResult(model=model)
        for case_id, passed in verdicts.items():
            trajectory = build_trajectory(_done(), seconds=0.0)
            result.results.append(
                score_case(_case(id=case_id), (), trajectory, model, None if passed else "x")
            )
        return result

    before = suite("a", {"one": True, "two": True, "three": False})
    after = suite("b", {"one": True, "two": False, "three": True, "four": True})
    assert compare(before, after) == [("four", "added"), ("three", "fixed"), ("two", "regressed")]


def test_the_json_record_keeps_every_check_for_a_later_diff(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    case = _case()
    trajectory = build_trajectory(_done(), seconds=0.5)
    suite = SuiteResult(model="m")
    suite.results.append(score_case(case, evaluate(case, trajectory, tmp_path), trajectory, "m", None))

    payload = to_dict(suite)
    assert payload["summary"]["cases"] == 1
    assert payload["cases"][0]["checks"]
    assert payload["summary"]["by_dimension"]


def test_the_matrix_lines_up_models_against_cases():
    suites = []
    for model, passed in (("gpt", True), ("claude", False)):
        suite = SuiteResult(model=model)
        trajectory = build_trajectory(_done(), seconds=0.0)
        suite.results.append(score_case(_case(), (), trajectory, model, None if passed else "x"))
        suites.append(suite)
    rendered = render_matrix(suites)
    assert "gpt" in rendered and "claude" in rendered
    assert "pass" in rendered and "FAIL" in rendered


def test_a_provider_failure_fails_the_case_even_though_the_stream_reached_done(tmp_path):
    """The trap that got past the first version of this harness.

    A relay rejected the configured model with HTTP 400. The engine did the
    right thing — emitted ``failed``, then ``done``, because the stream did
    terminate cleanly — and the case reported PASS: no tools called, so every
    "must not call" check passed, and DONE was read as success.

    The event sequence below is the real one, copied off that run.
    """
    trajectory = build_trajectory(
        [
            ("run_started", {}),
            ("context_usage", {}),
            ("thinking", {}),
            ("text_delta", {}),
            ("failed", {"error": "execution_error"}),
            ("done", {}),
            ("run_finished", {"status": "failed"}),
        ],
        seconds=2.4,
    )
    assert trajectory.completed is True, "DONE really was emitted — that is the trap"

    case = _case(expect={"tools": {"must_not_call": ["write_file"], "max_calls": 0}})
    checks = evaluate(case, trajectory, tmp_path)

    assert [check.dimension for check in checks] == [Dimension.COMPLETION]
    assert checks[0].passed is False
    assert "execution_error" in checks[0].detail


def test_a_clean_run_still_passes_completion(tmp_path):
    """The fix must not make every run fail: no failure event, no failure."""
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    checks = evaluate(_case(), build_trajectory(_done(), seconds=0.3), tmp_path)
    assert checks[0].passed is True
