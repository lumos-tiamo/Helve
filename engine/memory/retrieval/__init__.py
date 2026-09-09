"""Query-time memory retrieval.

``durable.md`` used to be injected whole on every turn.  This package splits it
into the bullets the writer already addresses individually, ranks them against
the turn's request, and renders back only what earns its place -- degrading to
the whole document on every failure path, because retrieval that silently drops
the bullet a turn needed is worse than one that costs tokens.
"""

from .chunk import Chunk, DURABLE_SECTIONS, chunk_document, render_chunks
from .retrieve import (
    DEFAULT_MAX_KEEP,
    DEFAULT_MIN_KEEP,
    Retrieval,
    RetrievalConfig,
    retrieve_memory,
)
from .score import BM25, Embedder, reciprocal_rank_fusion, tokenize

__all__ = [
    "Chunk", "DURABLE_SECTIONS", "chunk_document", "render_chunks",
    "Retrieval", "RetrievalConfig", "retrieve_memory",
    "DEFAULT_MIN_KEEP", "DEFAULT_MAX_KEEP",
    "BM25", "Embedder", "reciprocal_rank_fusion", "tokenize",
]
