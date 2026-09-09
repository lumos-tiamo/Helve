"""Splitting a rendered memory view into retrievable units.

The unit is one bullet.  That is not a chunking heuristic -- it is the shape the
document already has: ``_changeset`` writes, addresses, and evicts memory one
``- **topic**: content`` bullet at a time, so a bullet is the smallest thing
that is independently true.  Splitting anywhere else (fixed windows, sentences)
would cut a fact in half and retrieve a fragment that reads as a whole one.

Reusing ``parse_document`` rather than re-parsing matters for the same reason:
a retrieval layer that invents its own idea of the document's structure drifts
away from the writer's the first time the writer changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .._changeset import parse_document, topic_key

# Sections carry meaning worth keeping on the chunk: "Known Pitfalls" and
# "Active Work" answer different questions even with identical wording.
DURABLE_SECTIONS: tuple[str, ...] = (
    "Active Work",
    "Pending",
    "Verified Outcomes",
    "Decisions",
    "Known Pitfalls",
)

_TITLE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable memory bullet, addressed the way the writer addresses it."""

    section: str
    topic: str | None
    text: str
    ordinal: int

    @property
    def id(self) -> str:
        """Stable across recompiles as long as the topic survives.

        Falling back to the ordinal is deliberate: an untopiced bullet is still
        worth retrieving, it just cannot be tracked across an edit that moves it.
        """
        return f"{self.section}::{self.topic or f'#{self.ordinal}'}"

    @property
    def indexed_text(self) -> str:
        """What the index sees.

        The section name is folded in so a query like "pitfall" can reach a
        bullet that never uses the word, and the topic is repeated because it is
        the part a human would have searched for.
        """
        parts = [self.section]
        if self.topic:
            parts.append(self.topic)
        parts.append(self.text)
        return "\n".join(parts)


def chunk_document(text: str, sections: tuple[str, ...] = DURABLE_SECTIONS) -> list[Chunk]:
    """Split a rendered memory view into bullets, in document order."""
    if not text.strip():
        return []
    grouped = parse_document(text, sections)
    chunks: list[Chunk] = []
    ordinal = 0
    for section in sections:
        for bullet in grouped.get(section, []):
            if not bullet.strip():
                continue
            chunks.append(
                Chunk(section=section, topic=topic_key(bullet), text=bullet, ordinal=ordinal)
            )
            ordinal += 1
    return chunks


def document_title(text: str, default: str = "Project Memory") -> str:
    match = _TITLE.search(text)
    return match.group(1).strip() if match else default


def render_chunks(chunks: list[Chunk], title: str = "Project Memory") -> str:
    """Rebuild a document from selected chunks, preserving section order.

    The output is the same shape the model already knows how to read, so a
    retrieved view and a whole view are indistinguishable to the prompt -- the
    only difference is how many bullets are in it.
    """
    if not chunks:
        return ""
    by_section: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_section.setdefault(chunk.section, []).append(chunk)

    parts = [f"# {title}"]
    for section in DURABLE_SECTIONS:
        selected = by_section.get(section)
        if not selected:
            continue
        parts.extend(["", f"## {section}"])
        parts.extend(chunk.text for chunk in sorted(selected, key=lambda c: c.ordinal))
    parts.append("")
    return "\n".join(parts)
