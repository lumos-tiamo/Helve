"""Ranking memory chunks against a query.

Two scorers, combined.

**Lexical (BM25).**  Always available, no dependency, no network, no model.  It
is also the one that matters most here: memory bullets are short and written by
the same system that reads them, so a query and its answer usually share literal
tokens (a file path, an identifier, a library name).  Exactly the case BM25 is
good at and embeddings are wasteful for.

**Semantic (embeddings).**  Optional, and off unless configured.  It earns its
place on the queries BM25 cannot serve -- "how do we handle auth failures"
against a bullet that says "401 retries go through the refresh path" -- and pays
for it with a network round trip on the critical path.  A local-first tool does
not get to make that mandatory.

Fusion is Reciprocal Rank Fusion rather than a weighted score sum: the two
scorers produce numbers on incomparable scales (BM25 is unbounded, cosine is
[-1, 1]), and normalising them against each other invents a calibration nobody
measured.  RRF only needs the ranks, which are comparable by construction.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol, Sequence

# Standard BM25 parameters.  k1 controls term-frequency saturation, b the
# length normalisation.  Memory bullets are uniformly short, so b matters little.
BM25_K1 = 1.5
BM25_B = 0.75

# RRF's damping constant.  60 is the value from the original paper and the one
# every implementation uses; it keeps a rank-1 hit from dominating outright.
RRF_K = 60

_CJK = r"[㐀-䶿一-鿿぀-ヿ]"
_TOKEN = re.compile(r"[a-z0-9_]+|" + _CJK)
_IS_CJK = re.compile(_CJK)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, plus adjacent-CJK bigrams.

    Chinese has no spaces, so a whitespace tokenizer would turn a whole bullet
    into one token and BM25 into an exact-match test.  Single ideographs fix
    that but overcorrect: in a corpus of a few dozen short bullets, the function
    characters of 为什么 / 怎么 look statistically rare, so a query asking "why"
    scores against every bullet that happens to contain 什 or 么.  Observed
    live -- one unrelated bullet came back second for three different questions.

    Adding the bigram of each adjacent CJK pair gives the ranker a token that
    actually carries meaning (向量, sqlite 坑, 审批) alongside the characters,
    without a dictionary or a segmentation model.  Measured on the labelled set:
    MRR 0.771 -> 0.846, with top-3 precision unchanged.

    Real segmentation would beat this.  It would also mean shipping a dictionary
    into a local-first tool for a gain this already captures most of.
    """
    tokens = _TOKEN.findall(text.lower())
    bigrams = [
        left + right
        for left, right in zip(tokens, tokens[1:])
        if _IS_CJK.fullmatch(left) and _IS_CJK.fullmatch(right)
    ]
    return tokens + bigrams


@dataclass(frozen=True, slots=True)
class Scored:
    index: int
    score: float


class Embedder(Protocol):
    """Anything that can turn text into vectors.  Implementations live in ``embed``."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BM25:
    """Okapi BM25 over an in-memory corpus.

    The corpus is one memory document -- tens to low hundreds of bullets -- so
    scoring every chunk on every query costs microseconds and buys exact
    results.  An inverted index here would be machinery without a workload.
    """

    def __init__(self, corpus: Sequence[str]) -> None:
        self._docs = [tokenize(text) for text in corpus]
        self._lengths = [len(doc) for doc in self._docs]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if self._docs else 0.0
        self._counts = [Counter(doc) for doc in self._docs]

        document_frequency: Counter[str] = Counter()
        for counts in self._counts:
            document_frequency.update(counts.keys())

        total = len(self._docs)
        # Smoothed IDF, floored at zero: the raw form goes negative for a term
        # in more than half the documents, which would make a common term
        # actively subtract from a match.
        self._idf = {
            term: max(0.0, math.log(1.0 + (total - freq + 0.5) / (freq + 0.5)))
            for term, freq in document_frequency.items()
        }

    def score(self, query: str) -> list[float]:
        terms = tokenize(query)
        if not terms or not self._docs:
            return [0.0] * len(self._docs)

        scores = [0.0] * len(self._docs)
        for index, counts in enumerate(self._counts):
            length = self._lengths[index] or 1
            normalisation = BM25_K1 * (
                1 - BM25_B + BM25_B * (length / (self._avg_length or 1))
            )
            total = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                total += self._idf.get(term, 0.0) * (
                    frequency * (BM25_K1 + 1) / (frequency + normalisation)
                )
            scores[index] = total
        return scores


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def rank(scores: Sequence[float]) -> list[int]:
    """Indices ordered best-first, ties broken by document order for determinism."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], size: int, k: int = RRF_K
) -> list[Scored]:
    """Fuse several rankings of the same corpus into one.

    Only positions are used, so a scorer that is confidently wrong on magnitude
    cannot drown out one that is quietly right on order.
    """
    fused = [0.0] * size
    for ranking in rankings:
        for position, index in enumerate(ranking):
            if 0 <= index < size:
                fused[index] += 1.0 / (k + position + 1)
    return [Scored(index=i, score=fused[i]) for i in rank(fused)]
