"""Measuring what retrieval actually saves.

The claim "narrowing memory cuts prompt cost" is worth exactly as much as the
number behind it, and the number has to come from the same token estimator the
budget code uses -- counting characters and calling them tokens would produce a
figure that is confidently wrong for Chinese, which is most of this project's
memory content.

Run it:

    uv run python -m engine.memory.retrieval.measure ~/.helve/agent/memory/durable.md \\
        --query "sqlite wal 的坑" --query "怎么处理审批超时"

With no document it measures a synthetic one, which is honest about being
synthetic: a shape check, not a result to quote.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from engine.context.budget import estimate_tokens

from .chunk import chunk_document
from .retrieve import RetrievalConfig, retrieve_memory


@dataclass(frozen=True, slots=True)
class Measurement:
    query: str
    whole_tokens: int
    kept_tokens: int
    total_chunks: int
    kept_chunks: int

    @property
    def saved_tokens(self) -> int:
        return self.whole_tokens - self.kept_tokens

    @property
    def saved_ratio(self) -> float:
        return self.saved_tokens / self.whole_tokens if self.whole_tokens else 0.0


def measure(document: str, queries: list[str], config: RetrievalConfig | None = None) -> list[Measurement]:
    whole_tokens = estimate_tokens(document)
    total_chunks = len(chunk_document(document))
    results: list[Measurement] = []
    for query in queries:
        retrieval = retrieve_memory(document, query, config)
        results.append(
            Measurement(
                query=query,
                whole_tokens=whole_tokens,
                kept_tokens=estimate_tokens(retrieval.text),
                total_chunks=total_chunks,
                kept_chunks=len(retrieval.kept),
            )
        )
    return results


def synthetic_document(bullets: int = 60) -> str:
    """A stand-in shaped like a mature durable.md, for when there is no real one."""
    sections = {
        "Active Work": ["- **当前任务**: 在做记忆检索层"],
        "Decisions": [
            f"- **决策-{i}**: 选用方案 {i}，理由是它在这个规模下更简单" for i in range(bullets // 3)
        ],
        "Verified Outcomes": [
            f"- **结论-{i}**: 已验证第 {i} 条改动在 CI 上稳定通过" for i in range(bullets // 3)
        ],
        "Known Pitfalls": [
            f"- **坑-{i}**: sqlite wal 模式下第 {i} 个已知问题及其绕法" for i in range(bullets // 3)
        ],
    }
    parts = ["# Project Memory"]
    for name, items in sections.items():
        parts.extend(["", f"## {name}", *items])
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("document", nargs="?", type=Path, help="a durable.md; omit to use a synthetic one")
    parser.add_argument("--query", action="append", default=[], help="repeatable")
    parser.add_argument("--max-keep", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.document:
        document = args.document.read_text(encoding="utf-8")
        label = str(args.document)
    else:
        document = synthetic_document()
        label = "synthetic (a shape check, not a result to quote)"

    queries = args.query or ["sqlite wal 的坑", "我们当初为什么选这个方案", "现在在做什么"]
    config = RetrievalConfig(max_keep=args.max_keep) if args.max_keep else None
    results = measure(document, queries, config)

    if args.json:
        print(json.dumps([r.__dict__ | {"saved_ratio": r.saved_ratio} for r in results],
                         indent=2, ensure_ascii=False))
        return 0

    print(f"document: {label}")
    print(f"bullets:  {results[0].total_chunks}")
    print(f"whole:    {results[0].whole_tokens} tokens\n")
    for result in results:
        print(f"  query   {result.query}")
        print(
            f"          {result.kept_chunks}/{result.total_chunks} bullets · "
            f"{result.kept_tokens} tokens · saved {result.saved_tokens} "
            f"({result.saved_ratio:.0%})"
        )
    average = sum(r.saved_ratio for r in results) / len(results)
    print(f"\nmean saving across {len(results)} queries: {average:.0%}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
