"""Declarative evaluation cases.

A case is YAML, not Python, so adding a scenario costs a file rather than a
patch to the harness.  The schema is deliberately narrow: it can describe the
world a run starts in, the world it must end in, and the shape of the path
between the two.  It cannot describe what the model should *say* -- see
``assertions`` for why that is a feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class CaseError(ValueError):
    """A case document is malformed.  Raised at load time, never at run time."""


# Only these tools mutate anything the user owns, so only these are expected to
# pass through approval.  Kept here rather than imported from the engine: an
# eval that silently follows a change to the engine's own idea of "dangerous"
# cannot detect that change, which is exactly the regression worth catching.
WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "shell", "git_ops"})


@dataclass(frozen=True, slots=True)
class FileExpectation:
    """What one path must look like once the run finishes."""

    exists: bool = True
    contains: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    @classmethod
    def parse(cls, path: str, raw: Any) -> FileExpectation:
        if raw is None or raw is True:
            return cls()
        if raw is False:
            return cls(exists=False)
        if not isinstance(raw, dict):
            raise CaseError(f"expect.files[{path}] must be a mapping, true or false")
        unknown = set(raw) - {"exists", "contains", "absent"}
        if unknown:
            raise CaseError(f"expect.files[{path}] has unknown keys: {sorted(unknown)}")
        return cls(
            exists=bool(raw.get("exists", True)),
            contains=_string_tuple(raw.get("contains"), f"expect.files[{path}].contains"),
            absent=_string_tuple(raw.get("absent"), f"expect.files[{path}].absent"),
        )


@dataclass(frozen=True, slots=True)
class ToolExpectation:
    """The shape of the tool trajectory, not its exact sequence.

    Exact sequences are the wrong assertion for a model: reading a file twice
    before editing it is not a regression, and pinning the order turns every
    harmless reordering into a red run.  What is worth pinning is that the
    necessary tool ran, the forbidden one did not, and the count stayed sane.
    """

    must_call: tuple[str, ...] = ()
    must_not_call: tuple[str, ...] = ()
    max_calls: int | None = None

    @classmethod
    def parse(cls, raw: Any) -> ToolExpectation:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise CaseError("expect.tools must be a mapping")
        unknown = set(raw) - {"must_call", "must_not_call", "max_calls"}
        if unknown:
            raise CaseError(f"expect.tools has unknown keys: {sorted(unknown)}")
        max_calls = raw.get("max_calls")
        if max_calls is not None and (not isinstance(max_calls, int) or max_calls < 0):
            raise CaseError("expect.tools.max_calls must be a non-negative integer")
        return cls(
            must_call=_string_tuple(raw.get("must_call"), "expect.tools.must_call"),
            must_not_call=_string_tuple(raw.get("must_not_call"), "expect.tools.must_not_call"),
            max_calls=max_calls,
        )


@dataclass(frozen=True, slots=True)
class Budget:
    """Ceilings that make a run a failure even when its output is right.

    A correct answer that took forty tool calls is a regression the world-state
    assertions cannot see.
    """

    max_tool_calls: int | None = None
    max_seconds: float | None = None
    max_total_tokens: int | None = None

    @classmethod
    def parse(cls, raw: Any) -> Budget:
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise CaseError("budget must be a mapping")
        unknown = set(raw) - {"max_tool_calls", "max_seconds", "max_total_tokens"}
        if unknown:
            raise CaseError(f"budget has unknown keys: {sorted(unknown)}")
        return cls(
            max_tool_calls=_positive_int(raw.get("max_tool_calls"), "budget.max_tool_calls"),
            max_seconds=_positive_float(raw.get("max_seconds"), "budget.max_seconds"),
            max_total_tokens=_positive_int(raw.get("max_total_tokens"), "budget.max_total_tokens"),
        )


@dataclass(frozen=True, slots=True)
class Case:
    """One scenario: a prompt, the world it runs against, and what must hold after."""

    id: str
    prompt: str
    description: str = ""
    identity: str = "smith"
    tags: tuple[str, ...] = ()
    # Files written into the workspace before the run.
    given_files: dict[str, str] = field(default_factory=dict)
    expect_files: dict[str, FileExpectation] = field(default_factory=dict)
    expect_tools: ToolExpectation = field(default_factory=ToolExpectation)
    # Tools whose invocation must have gone through the approval broker.  Left
    # empty the harness infers it from WRITE_TOOLS actually called, so a case
    # only states this when it wants something stricter.
    expect_approval_for: tuple[str, ...] | None = None
    expect_blocked: bool = False
    budget: Budget = field(default_factory=Budget)
    source: Path | None = None

    @property
    def label(self) -> str:
        return f"{self.id}"


_TOP_LEVEL_KEYS = {
    "id", "prompt", "description", "identity", "tags",
    "given", "expect", "budget",
}


def parse_case(raw: Any, source: Path | None = None) -> Case:
    """Build a Case from a parsed YAML document, rejecting anything unclear."""
    if not isinstance(raw, dict):
        raise CaseError(f"{source or '<case>'}: a case must be a YAML mapping")
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise CaseError(f"{source or '<case>'}: unknown top-level keys: {sorted(unknown)}")

    case_id = _required_string(raw.get("id"), "id", source)
    prompt = _required_string(raw.get("prompt"), "prompt", source)

    given = raw.get("given") or {}
    if not isinstance(given, dict):
        raise CaseError(f"{source or case_id}: given must be a mapping")
    given_unknown = set(given) - {"files"}
    if given_unknown:
        raise CaseError(f"{source or case_id}: given has unknown keys: {sorted(given_unknown)}")
    given_files = given.get("files") or {}
    if not isinstance(given_files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in given_files.items()
    ):
        raise CaseError(f"{source or case_id}: given.files must map path -> text")
    for rel in given_files:
        _reject_escaping_path(rel, f"{source or case_id}: given.files")

    expect = raw.get("expect") or {}
    if not isinstance(expect, dict):
        raise CaseError(f"{source or case_id}: expect must be a mapping")
    expect_unknown = set(expect) - {"files", "tools", "approval_for", "blocked"}
    if expect_unknown:
        raise CaseError(f"{source or case_id}: expect has unknown keys: {sorted(expect_unknown)}")

    raw_files = expect.get("files") or {}
    if not isinstance(raw_files, dict):
        raise CaseError(f"{source or case_id}: expect.files must be a mapping")
    for rel in raw_files:
        _reject_escaping_path(rel, f"{source or case_id}: expect.files")

    approval_for = expect.get("approval_for")
    if approval_for is not None:
        approval_for = _string_tuple(approval_for, "expect.approval_for")

    return Case(
        id=case_id,
        prompt=prompt,
        description=str(raw.get("description") or "").strip(),
        identity=str(raw.get("identity") or "smith"),
        tags=_string_tuple(raw.get("tags"), "tags"),
        given_files=dict(given_files),
        expect_files={
            path: FileExpectation.parse(path, value) for path, value in raw_files.items()
        },
        expect_tools=ToolExpectation.parse(expect.get("tools")),
        expect_approval_for=approval_for,
        expect_blocked=bool(expect.get("blocked", False)),
        budget=Budget.parse(raw.get("budget")),
        source=source,
    )


def load_suite(path: Path) -> list[Case]:
    """Load every case under *path* -- one file, or a directory of them."""
    files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
    if not files:
        raise CaseError(f"no case files found under {path}")

    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for file in files:
        for document in yaml.safe_load_all(file.read_text(encoding="utf-8")):
            if document is None:
                continue
            case = parse_case(document, source=file)
            # Duplicate ids would silently overwrite each other in the report
            # and make a suite quietly smaller than it looks.
            if case.id in seen:
                raise CaseError(f"duplicate case id {case.id!r} in {file} and {seen[case.id]}")
            seen[case.id] = file
            cases.append(case)
    return cases


# ── helpers ────────────────────────────────────────────────────────────────

def _required_string(value: Any, field_name: str, source: Path | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{source or '<case>'}: {field_name} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CaseError(f"{field_name} must be a string or a list of strings")
    return tuple(value)


def _positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CaseError(f"{field_name} must be a positive integer")
    return value


def _positive_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CaseError(f"{field_name} must be a positive number")
    return float(value)


def _reject_escaping_path(rel: str, context: str) -> None:
    """A case controls paths, and a case file is content -- treat it as such.

    ``../`` or an absolute path here would let a suite read or write outside the
    throwaway workspace, on the machine of whoever runs the suite.
    """
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise CaseError(f"{context}: {rel!r} must stay inside the workspace")
