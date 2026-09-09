"""Query-time memory retrieval.

The property under test throughout is not "does it rank well" -- it is **does it
ever lose a fact without saying so**.  Ranking that is merely mediocre costs a
few tokens; a bullet that silently vanishes makes the agent forget something it
was told, with no error anywhere.  So most of what follows pins the fallbacks.
"""

from __future__ import annotations

import pytest

from engine.memory.retrieval import (
    BM25,
    RetrievalConfig,
    chunk_document,
    render_chunks,
    retrieve_memory,
    tokenize,
)


def _document(bullets_per_section: int = 8) -> str:
    sections = {
        "Active Work": [f"- **active-{i}**: 正在做的第 {i} 件事" for i in range(2)],
        "Decisions": [
            f"- **decision-{i}**: 决定用方案 {i}，因为它更简单" for i in range(bullets_per_section)
        ],
        "Known Pitfalls": [
            f"- **pitfall-{i}**: sqlite wal 模式下第 {i} 个坑" for i in range(bullets_per_section)
        ],
    }
    parts = ["# Project Memory"]
    for name, bullets in sections.items():
        parts.extend(["", f"## {name}", *bullets])
    return "\n".join(parts) + "\n"


# ── chunking ───────────────────────────────────────────────────────────────

def test_a_bullet_is_the_unit_and_keeps_its_section():
    chunks = chunk_document(_document(2))
    assert [chunk.section for chunk in chunks[:2]] == ["Active Work", "Active Work"]
    assert chunks[0].topic == "active-0"
    # The section rides along in what the index sees, so "pitfall" can reach a
    # bullet that never uses the word.
    assert "Known Pitfalls" in chunks[-1].indexed_text


def test_rendering_selected_chunks_produces_the_same_shape_as_the_source():
    """A narrowed view and a whole view must be indistinguishable to the prompt."""
    document = _document(3)
    rebuilt = render_chunks(chunk_document(document), "Project Memory")
    assert chunk_document(rebuilt) == chunk_document(document)
    assert rebuilt.startswith("# Project Memory")
    assert "## Decisions" in rebuilt


def test_an_unparseable_document_yields_no_chunks_rather_than_a_wrong_one():
    assert chunk_document("just some prose, no sections at all") == []


# ── the fallbacks that matter ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "document, query, reason_fragment",
    [
        (_document(20), "", "empty query"),
        ("", "anything", "empty document"),
        ("no bullets here", "anything", "no bullets parsed"),
        (_document(1), "decision", "under the floor"),
    ],
)
def test_every_degenerate_input_falls_back_to_the_whole_document(
    document, query, reason_fragment
):
    result = retrieve_memory(document, query)
    assert result.text == document
    assert result.strategy == "whole"
    assert reason_fragment in result.reason


def test_the_disabled_switch_restores_whole_injection_exactly():
    """The A/B control: both behaviours must be reachable on one build."""
    document = _document(30)
    result = retrieve_memory(document, "sqlite wal", RetrievalConfig(enabled=False))
    assert result.text == document
    assert result.saved_chars == 0


def test_a_document_that_fits_the_budget_is_passed_through_byte_for_byte():
    document = _document(4)  # 2 + 4 + 4 = 10 bullets, under the default max
    result = retrieve_memory(document, "decision")
    assert result.text == document
    assert result.strategy == "whole"


def test_a_failing_embedder_costs_relevance_not_the_turn():
    class Exploding:
        def embed(self, texts):
            raise RuntimeError("embedding endpoint is down")

    document = _document(20)
    result = retrieve_memory(
        document, "sqlite wal 坑", RetrievalConfig(embedder=Exploding())
    )
    # Still narrowed, still coherent -- just ranked lexically.
    assert result.strategy == "hybrid"
    assert result.kept
    assert chunk_document(result.text)


def test_an_embedder_returning_the_wrong_count_is_ignored():
    """A silently truncated batch would misalign every vector with its chunk."""
    class Truncating:
        def embed(self, texts):
            return [[1.0, 0.0]] * (len(texts) - 2)

    result = retrieve_memory(_document(20), "decision", RetrievalConfig(embedder=Truncating()))
    assert result.kept


# ── selection behaviour ────────────────────────────────────────────────────

def test_narrowing_keeps_the_relevant_bullets_and_saves_tokens():
    document = _document(30)
    result = retrieve_memory(document, "sqlite wal 模式的坑")

    assert result.strategy == "lexical"
    assert len(result.kept) < result.total_chunks
    assert result.saved_chars > 0
    # The query is about pitfalls, so pitfalls should dominate what survived.
    pitfalls = sum(1 for chunk in result.kept if chunk.section == "Known Pitfalls")
    decisions = sum(1 for chunk in result.kept if chunk.section == "Decisions")
    assert pitfalls > decisions


def test_active_work_is_never_dropped():
    """What the user is doing right now is relevant by definition.

    Making the ranker rediscover that every turn is how a live task falls out of
    context on a query that happens not to share its wording.
    """
    document = _document(40)
    result = retrieve_memory(document, "完全无关的查询 xyzzy")
    kept_sections = {chunk.section for chunk in result.kept}
    assert "Active Work" in kept_sections
    assert all(chunk.section != "Active Work" for chunk in result.dropped)


def test_the_budget_is_respected():
    result = retrieve_memory(_document(40), "decision", RetrievalConfig(max_keep=12))
    assert len(result.kept) <= 12


def test_selection_is_deterministic():
    """Two identical calls must inject identical prompts, or caching is a lie."""
    document = _document(30)
    first = retrieve_memory(document, "sqlite")
    second = retrieve_memory(document, "sqlite")
    assert first.text == second.text


def test_the_decision_is_traceable():
    trace = retrieve_memory(_document(30), "pitfall").to_trace_data()
    assert trace["chunks_kept"] < trace["chunks_total"]
    assert 0.0 < trace["saved_ratio"] < 1.0
    assert trace["kept_ids"]


# ── scoring ────────────────────────────────────────────────────────────────

def test_chinese_is_tokenized_per_ideograph_so_partial_overlap_scores():
    """A whitespace tokenizer makes BM25 an exact-match test on Chinese."""
    assert tokenize("sqlite 的坑") == ["sqlite", "的", "坑"]


def test_bm25_ranks_the_matching_document_first():
    corpus = ["sqlite wal mode pitfall", "react hooks rendering", "postgres index tuning"]
    scores = BM25(corpus).score("sqlite wal")
    assert scores[0] > scores[1] and scores[0] > scores[2]


def test_a_term_in_every_document_cannot_subtract_from_a_match():
    """Raw BM25 IDF goes negative past 50% document frequency."""
    corpus = ["common term here", "common term there", "common term everywhere"]
    assert all(score >= 0.0 for score in BM25(corpus).score("common"))


def test_an_empty_query_scores_nothing_rather_than_erroring():
    assert BM25(["a b c"]).score("") == [0.0]
