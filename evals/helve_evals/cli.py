"""`helve-eval` — run a suite, print what broke, leave a JSON record behind."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from .assertions import evaluate
from .case import Case, CaseError, load_suite
from .report import render_console, render_matrix, render_regressions, write_json
from .runner import materialize_workspace, run_case
from .scoring import SuiteResult, score_case

_DEFAULT_SUITE = Path(__file__).resolve().parent.parent / "suites"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="helve-eval",
        description="Run Helve scenario evaluations against one or more models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every case calls a real provider and therefore costs money.  There is no\n"
            "offline mode here on purpose: replaying recorded turns measures harness\n"
            "logic, not model capability, and pretending otherwise is how a suite ends\n"
            "up reporting a score for a prompt it never sent.  Harness-logic regression\n"
            "lives in engine/tests via engine.llm.replay."
        ),
    )
    parser.add_argument("--suite", type=Path, default=_DEFAULT_SUITE, help="case file or directory")
    parser.add_argument("--select", action="append", default=[], metavar="ID", help="run only these case ids")
    parser.add_argument("--tag", action="append", default=[], metavar="TAG", help="run only cases with these tags")
    parser.add_argument(
        "--model", action="append", default=[], metavar="NAME",
        help="model to evaluate; repeat for a comparison matrix (default: the configured model)",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="per-case ceiling in seconds")
    parser.add_argument("--json", type=Path, metavar="PATH", help="write the full record here")
    parser.add_argument("--baseline", type=Path, metavar="PATH", help="a previous --json record to diff against")
    parser.add_argument("--workspace", type=Path, help="keep case workspaces here instead of a temp dir")
    parser.add_argument("--verbose", action="store_true", help="show passing checks too")
    parser.add_argument("--no-colour", action="store_true")
    parser.add_argument("--list", action="store_true", help="print the selected cases and exit")
    return parser


def select_cases(cases: list[Case], ids: list[str], tags: list[str]) -> list[Case]:
    selected = cases
    if ids:
        wanted = set(ids)
        selected = [case for case in selected if case.id in wanted]
        missing = wanted - {case.id for case in selected}
        if missing:
            raise CaseError(f"no such case id: {', '.join(sorted(missing))}")
    if tags:
        wanted_tags = set(tags)
        selected = [case for case in selected if wanted_tags & set(case.tags)]
    return selected


async def _run_suite(cases: list[Case], model: str, root: Path, timeout: float) -> SuiteResult:
    suite = SuiteResult(model=model)
    for case in cases:
        workspace = materialize_workspace(case, root / model.replace("/", "_"))
        outcome = await run_case(case, workspace, timeout=timeout)
        checks = evaluate(case, outcome.trajectory, workspace)
        suite.results.append(
            score_case(case, checks, outcome.trajectory, model, outcome.error)
        )
    return suite


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cases = select_cases(load_suite(args.suite), args.select, args.tag)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not cases:
        print("error: no cases selected", file=sys.stderr)
        return 2

    if args.list:
        for case in cases:
            tags = f"  [{', '.join(case.tags)}]" if case.tags else ""
            print(f"{case.id}{tags}\n    {case.description or case.prompt}")
        return 0

    configured = os.environ.get("HELVE_LLM_MODEL")
    models = args.model or [configured or "configured"]
    if not configured and not args.model:
        print(
            "error: no model configured.  Set HELVE_LLM_API_KEY / HELVE_LLM_MODEL, or pass --model.",
            file=sys.stderr,
        )
        return 2

    temporary = None
    if args.workspace:
        root = args.workspace
        root.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="helve-eval-")
        root = Path(temporary.name)

    try:
        suites: list[SuiteResult] = []
        for model in models:
            # Model selection rides the same env override the engine already
            # reads, so the harness needs no privileged path into config.
            if args.model:
                os.environ["HELVE_LLM_MODEL"] = model
            suite = asyncio.run(_run_suite(cases, model, root, args.timeout))
            suites.append(suite)
            print(render_console(suite, colour=not args.no_colour, verbose=args.verbose))
            print()
    finally:
        if configured is not None:
            os.environ["HELVE_LLM_MODEL"] = configured
        if temporary is not None and not args.workspace:
            temporary.cleanup()

    if len(suites) > 1:
        print(render_matrix(suites))
        print()

    if args.json:
        write_json(suites[-1], args.json)
        print(f"wrote {args.json}")

    if args.baseline:
        baseline = _load_baseline(args.baseline)
        if baseline is not None:
            print()
            print(render_regressions(baseline, suites[-1]))

    return 0 if all(suite.passed == suite.total for suite in suites) else 1


def _load_baseline(path: Path) -> SuiteResult | None:
    """Rebuild just enough of a previous record to diff verdicts against it."""
    import json

    from .scoring import CaseResult
    from .trajectory import Trajectory

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not read baseline {path}: {exc}", file=sys.stderr)
        return None

    suite = SuiteResult(model=str(raw.get("model", "baseline")))
    for entry in raw.get("cases", []):
        suite.results.append(
            CaseResult(
                case_id=str(entry.get("id", "?")),
                model=suite.model,
                checks=(),
                trajectory=Trajectory(),
                error=None if entry.get("passed") else "recorded failure",
            )
        )
    return suite


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
