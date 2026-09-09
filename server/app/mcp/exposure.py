"""What Helve offers to other agents, and the rule that decides it.

Helve has always been an MCP *client*.  This is the other direction: Claude
Code, Cursor, or any MCP host can call into it.

**The exposed surface is knowledge, not action, and that is a design decision
rather than a first-version shortcut.** Every mutating tool in this runtime
pauses for a human — that is the whole thesis of the product. An MCP caller has
no human at the other end, so exposing a write tool would mean either blocking
forever on an approval nobody will answer, or quietly dropping the approval and
turning the guard into decoration. Neither is acceptable, so writes stay behind
the terminal.

Read-only *filesystem* tools are excluded for a second, independent reason: an
external caller carries no workspace. ``read_file`` without a bound working
directory is either useless or a path-traversal surface, and inventing a
workspace for a caller that never declared one is how a guard gets bypassed by
accident. If that changes, it needs an explicit workspace handshake, not a
widened allowlist.

What is left is what Helve actually knows: its accumulated project memory, and
the skills it can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from common import config as common_config
from engine.memory.retrieval import RetrievalConfig, retrieve_memory

# Bounds on one memory_search call.  A caller asking for 500 bullets is asking
# for the whole document by another name, which is the thing retrieval exists
# to avoid.
_MIN_RESULTS = 1
_MAX_RESULTS = 25
_DEFAULT_RESULTS = 8


class McpToolError(Exception):
    """A tool call that failed for a reason the caller can act on."""


@dataclass(frozen=True, slots=True)
class ExposedTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _memory_dir() -> Path:
    return common_config.PATHS.agent_dir / "memory"


def _durable_path() -> Path:
    return _memory_dir() / "durable.md"


def _memory_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise McpToolError("query must be a non-empty string")

    limit = arguments.get("limit", _DEFAULT_RESULTS)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise McpToolError("limit must be an integer")
    limit = max(_MIN_RESULTS, min(_MAX_RESULTS, limit))

    path = _durable_path()
    if not path.is_file():
        return {"bullets": [], "note": "this install has no project memory yet"}

    document = path.read_text(encoding="utf-8")
    # max_keep is the caller's limit, but min_keep stays at the default: below
    # the floor the retriever returns everything, and a caller asking for 1
    # result out of a 6-bullet document should still get a coherent answer
    # rather than an arbitrary single line.
    result = retrieve_memory(document, query, RetrievalConfig(max_keep=limit))
    return {
        "bullets": [
            {"section": chunk.section, "topic": chunk.topic, "text": chunk.text}
            for chunk in result.kept
        ],
        "strategy": result.strategy,
        "total_bullets": result.total_chunks,
        "returned": len(result.kept),
    }


def _skills_list(_arguments: dict[str, Any]) -> dict[str, Any]:
    """The skills this Helve can run.

    Built through ``SkillRegistry`` rather than by scanning directories, so an
    invalid skill is skipped here exactly as it is at run time and this list
    cannot drift from what the agent would actually load.
    """
    from engine.skill.registry import SkillRegistry

    paths = common_config.PATHS
    registry = SkillRegistry()
    registry.load_builtin(paths.builtin_skills_dir)
    agent_skills = paths.agent_dir / "skills"
    if agent_skills.is_dir():
        registry.load_agent_skills(agent_skills)

    summaries = sorted(registry.list_summaries(), key=lambda item: str(item.get("name", "")))
    return {"skills": summaries, "count": len(summaries)}


EXPOSED_TOOLS: tuple[ExposedTool, ...] = (
    ExposedTool(
        name="memory_search",
        description=(
            "Search this Helve install's accumulated project memory and return the "
            "most relevant entries. Memory holds verified outcomes, decisions and "
            "known pitfalls compiled from earlier sessions — not raw transcripts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you want to know about this project.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum entries to return ({_MIN_RESULTS}-{_MAX_RESULTS}).",
                    "minimum": _MIN_RESULTS,
                    "maximum": _MAX_RESULTS,
                    "default": _DEFAULT_RESULTS,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_memory_search,
    ),
    ExposedTool(
        name="skills_list",
        description=(
            "List the skills this Helve install can run, with what each is for."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_skills_list,
    ),
)

TOOLS_BY_NAME: dict[str, ExposedTool] = {tool.name: tool for tool in EXPOSED_TOOLS}
