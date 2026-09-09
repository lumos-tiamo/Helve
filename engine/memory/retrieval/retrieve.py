"""Query-time selection of memory bullets.

Before this module, ``durable.md`` was injected whole on every turn: prompt cost
grew with everything the agent had ever learned, and relevance was not a
concept.  Here the same document is split into bullets, ranked against the turn's
actual request, and only what earns its place is rendered back.

Three properties the design is built around, in order of how much they matter:

**Never lose a fact silently.**  Retrieval that drops the one bullet the turn
needed is worse than injecting everything, because the failure is invisible --
the model simply does not know a thing it used to know.  So the budget is a
*floor on recall*, not a hard cap: below ``min_keep`` bullets nothing is
dropped at all, and a document that already fits the budget is passed through
untouched, byte for byte.

**Degrade to the old behaviour, never to nothing.**  Every failure path here
returns the whole document.  A broken embedder, an empty query, an unparseable
document: all of them fall back to what the system did before, which is known
to work and merely costs tokens.

**Stay measurable.**  Every call returns a ``Retrieval`` carrying what was kept,
what was dropped and both token estimates, so "does this actually save
anything?" is a question the caller can answer instead of assume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .chunk import Chunk, chunk_document, document_title, render_chunks
from .score import BM25, Embedder, Scored, cosine, rank, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# Below this many bullets a document is not worth thinning: the tokens saved are
# noise next to the risk of dropping the one that mattered.
DEFAULT_MIN_KEEP = 8

# The ceiling on what retrieval will inject.  Chosen to sit well under the
# document sizes where whole-injection starts to hurt, while staying generous
# enough that a normal turn sees everything relevant.
DEFAULT_MAX_KEEP = 24


@dataclass(frozen=True, slots=True)
class Retrieval:
    """What retrieval decided, and the evidence for judging whether it was right."""

    text: str
    kept: tuple[Chunk, ...] = ()
    dropped: tuple[Chunk, ...] = ()
    total_chunks: int = 0
    whole_chars: int = 0
    kept_chars: int = 0
    strategy: str = "whole"
    reason: str = ""

    @property
    def saved_chars(self) -> int:
        return max(0, self.whole_chars - self.kept_chars)

    @property
    def saved_ratio(self) -> float:
        return self.saved_chars / self.whole_chars if self.whole_chars else 0.0

    def to_trace_data(self) -> dict[str, object]:
        """Small enough to attach to every run's trace without bloating it."""
        return {
            "strategy": self.strategy,
            "reason": self.reason,
            "chunks_total": self.total_chunks,
            "chunks_kept": len(self.kept),
            "chars_whole": self.whole_chars,
            "chars_kept": self.kept_chars,
            "saved_ratio": round(self.saved_ratio, 4),
            "kept_ids": [chunk.id for chunk in self.kept],
        }


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Knobs, with the defaults a local-first install should have.

    ``enabled=False`` is the A/B switch: it restores whole-document injection
    exactly, so the two behaviours can be compared on the same build rather
    than across a revert.
    """

    enabled: bool = True
    min_keep: int = DEFAULT_MIN_KEEP
    max_keep: int = DEFAULT_MAX_KEEP
    embedder: Embedder | None = None
    # Sections whose bullets are always injected regardless of score.  Active
    # work is what the user is doing *right now*: it is relevant by definition
    # and no ranker should have to rediscover that every turn.
    always_keep_sections: tuple[str, ...] = ("Active Work",)


def retrieve_memory(
    document: str,
    query: str,
    config: RetrievalConfig | None = None,
) -> Retrieval:
    """Select the bullets of *document* worth injecting for *query*.

    Returns the whole document unchanged whenever selection cannot be done
    safely -- see the module docstring for why that is the only acceptable
    failure mode.
    """
    config = config or RetrievalConfig()
    whole_chars = len(document)

    if not config.enabled:
        return _whole(document, "retrieval disabled")
    if not document.strip():
        return Retrieval(text=document, whole_chars=whole_chars, kept_chars=whole_chars,
                         strategy="whole", reason="empty document")
    if not query.strip():
        return _whole(document, "empty query")

    chunks = chunk_document(document)
    if not chunks:
        # An unparseable document is not an empty one.  Passing it through keeps
        # a format change from silently erasing the agent's memory.
        return _whole(document, "no bullets parsed")
    if len(chunks) <= config.min_keep:
        return _whole(document, f"{len(chunks)} bullets is under the floor")

    pinned_indices = {
        index for index, chunk in enumerate(chunks)
        if chunk.section in config.always_keep_sections
    }
    candidates = [index for index in range(len(chunks)) if index not in pinned_indices]
    room = max(0, config.max_keep - len(pinned_indices))
    if room >= len(candidates):
        return _whole(document, "budget exceeds the document")

    ordered = _rank_candidates(chunks, candidates, query, config)
    selected = pinned_indices | {scored.index for scored in ordered[:room]}

    kept = [chunk for index, chunk in enumerate(chunks) if index in selected]
    dropped = [chunk for index, chunk in enumerate(chunks) if index not in selected]
    text = render_chunks(kept, document_title(document))

    return Retrieval(
        text=text,
        kept=tuple(kept),
        dropped=tuple(dropped),
        total_chunks=len(chunks),
        whole_chars=whole_chars,
        kept_chars=len(text),
        strategy="hybrid" if config.embedder is not None else "lexical",
        reason=f"kept {len(kept)}/{len(chunks)} bullets",
    )


def _rank_candidates(
    chunks: list[Chunk], candidates: list[int], query: str, config: RetrievalConfig
) -> list[Scored]:
    """Rank the non-pinned bullets, lexically and -- if configured -- semantically."""
    corpus = [chunks[index].indexed_text for index in candidates]
    lexical = BM25(corpus).score(query)
    rankings = [[candidates[i] for i in rank(lexical)]]

    semantic = _semantic_ranking(corpus, candidates, query, config.embedder)
    if semantic is not None:
        rankings.append(semantic)

    fused = reciprocal_rank_fusion(rankings, size=len(chunks))
    allowed = set(candidates)
    return [scored for scored in fused if scored.index in allowed]


def _semantic_ranking(
    corpus: list[str], candidates: list[int], query: str, embedder: Embedder | None
) -> list[int] | None:
    """A semantic ranking, or None when one cannot be produced.

    Every failure returns None rather than raising: a flaky embedding endpoint
    must cost relevance, never the turn.
    """
    if embedder is None:
        return None
    try:
        vectors = embedder.embed([query, *corpus])
    except Exception as exc:  # noqa: BLE001 - any embedder failure degrades to lexical
        logger.warning("memory retrieval: embedder failed, using lexical only: %s", exc)
        return None
    if len(vectors) != len(corpus) + 1:
        logger.warning(
            "memory retrieval: embedder returned %d vectors for %d inputs; using lexical only",
            len(vectors), len(corpus) + 1,
        )
        return None

    query_vector, chunk_vectors = vectors[0], vectors[1:]
    scores = [cosine(query_vector, vector) for vector in chunk_vectors]
    return [candidates[i] for i in rank(scores)]


def _whole(document: str, reason: str) -> Retrieval:
    chunks = chunk_document(document)
    return Retrieval(
        text=document,
        kept=tuple(chunks),
        dropped=(),
        total_chunks=len(chunks),
        whole_chars=len(document),
        kept_chars=len(document),
        strategy="whole",
        reason=reason,
    )
