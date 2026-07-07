"""Chunk DSD prose sections into search/embedding units (docs §4, §6).

Pure ``list[ProseSection] -> list[Chunk]`` transform. This is the step *after*
:func:`app.clients.dart.extract_dsd_prose` and *before* embedding + DB load:
embedding, ``filing_chunks`` writes, and the KURE-v1 vectors are all the NEXT
step -- nothing here touches the network or the database.

Core project rules honoured here:
- **citation-grounded**: every :class:`Chunk` carries ``rcept_no`` (which filing),
  ``section_title``/``section_order`` (which section) and ``part_index`` (which
  slice of that section), so any embedded text is always traceable to its source.
- **numbers come only from the financials API (§3)**: chunking operates on prose
  that already had every ``<TABLE>`` excluded upstream, so there is no numeric
  judgement here at all.
- **when ambiguous, skip + log -- never invent**: an empty-content section
  produces no chunk and is logged, rather than being padded or guessed at.

Chunk -> ``filing_chunks`` mapping (the NEXT step wires this to the DB, docs §6):
    filing_chunks.content     <- Chunk.content
    filing_chunks.chunk_index <- Chunk.chunk_index
    filing_chunks.meta        <- {rcept_no, section_title, section_order,
                                  part_index}   (citation anchor)

Design source of truth: docs/dart-api-notes.md §4 (청킹 설계 메모) and §6
(filing_chunks 매핑). The chunking rules below are the "확정된 청킹 설계".
"""

import logging
import statistics
from dataclasses import dataclass

from app.clients.dart import ProseSection

logger = logging.getLogger(__name__)

# Chunk-size budget, in *characters* (not tokens). We size by character count on
# purpose [Inferred]: a token count would pull in a Korean tokenizer dependency,
# and for a first cut the search/embedding unit only needs to be "roughly a
# paragraph or two". These are initial values -- revisit once retrieval quality
# is measured on real filings (kept as module constants, not config: promoting
# them to config.py now would be over-engineering for three numbers).
TARGET_CHARS = 800  # accumulate paragraphs until a chunk reaches ~this size
MAX_CHARS = 1200  # hard upper bound: no chunk may exceed this
OVERLAP_CHARS = 100  # context overlap, used ONLY when force-splitting one long paragraph


@dataclass(frozen=True)
class Chunk:
    """One search/embedding unit carved from a :class:`ProseSection` (docs §6).

    The citation anchor travels with the text: ``rcept_no`` says which filing,
    ``section_title``/``section_order`` which section, and ``part_index`` which
    slice within that section. ``chunk_index`` is the document-wide running index
    (0..N-1) that maps directly onto ``filing_chunks.chunk_index`` (which is
    unique per filing, see models.py).

    Chunk -> ``filing_chunks`` mapping (docs §6):
        content     <- content
        chunk_index <- chunk_index
        meta        <- {rcept_no, section_title, section_order, part_index}
    """

    content: str
    chunk_index: int  # running index across the whole document (0..N-1)
    # DART receipt number (which filing). None for SEC filings, which have no
    # rcept_no -- their provenance rides on filing_id -> filings.sec_accession_no,
    # so the accession is never mislabeled into this DART-specific field.
    rcept_no: str | None
    section_title: str | None
    section_order: int  # source ProseSection.order
    part_index: int  # 0-based slice number *within* this section


def _force_split_paragraph(paragraph: str) -> list[str]:
    """Hard-split one paragraph longer than :data:`MAX_CHARS` by characters.

    This is the exceptional path: a single paragraph that on its own exceeds the
    hard cap cannot be placed whole, so we cut it into ``MAX_CHARS``-wide windows.
    Consecutive windows overlap by :data:`OVERLAP_CHARS` characters so a sentence
    split across the boundary still has its context in both pieces -- overlap is
    applied *only* here (a paragraph-boundary cut needs none, the boundary is
    already a meaning unit). ``MAX_CHARS - OVERLAP_CHARS > 0`` guarantees forward
    progress. Pure -> unit-tested.
    """
    step = MAX_CHARS - OVERLAP_CHARS  # > 0 -> the window always advances
    parts: list[str] = []
    start = 0
    while start < len(paragraph):
        parts.append(paragraph[start : start + MAX_CHARS])
        if start + MAX_CHARS >= len(paragraph):
            break
        start += step
    return parts


def split_section(section: ProseSection) -> list[str]:
    """Split one section's prose into chunk-sized strings (docs §4 청킹 설계).

    The section's ``content`` is paragraphs joined by ``\\n`` (see
    :func:`app.clients.dart._collect_section_prose`). We respect that boundary:

    1. Accumulate whole paragraphs in order; once the running length reaches
       :data:`TARGET_CHARS`, cut a chunk. Short sections (<= target) stay one
       chunk; only long sections split.
    2. Never let an accumulated chunk exceed :data:`MAX_CHARS`: if adding the next
       paragraph would cross the cap, flush what we have first and start fresh.
    3. A single paragraph longer than the cap is force-split by characters (with
       overlap) via :func:`_force_split_paragraph` -- the only place overlap is
       used, since a paragraph-boundary cut is already a clean meaning unit.

    An empty (or whitespace-only) ``content`` yields ``[]``. Pure -> unit-tested.
    """
    # Drop blank paragraphs defensively; upstream normalizes each <P> and joins
    # non-empty ones with "\n", so this is just belt-and-braces.
    paragraphs = [p for p in section.content.split("\n") if p.strip()]

    chunks: list[str] = []
    current: list[str] = []  # paragraphs accumulated for the in-progress chunk
    current_len = 0  # len("\n".join(current)), kept incrementally

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    for paragraph in paragraphs:
        # A lone paragraph over the hard cap: flush the buffer, then char-split it.
        if len(paragraph) > MAX_CHARS:
            flush()
            chunks.extend(_force_split_paragraph(paragraph))
            continue

        # +1 for the "\n" that will join this paragraph to the buffer.
        separator = 1 if current else 0
        if current and current_len + separator + len(paragraph) > MAX_CHARS:
            # Adding it would breach the cap -> emit the buffer, start anew.
            flush()
            separator = 0

        current.append(paragraph)
        current_len += separator + len(paragraph)

        # Reached the target size -> cut here (paragraph boundary, no overlap).
        if current_len >= TARGET_CHARS:
            flush()

    flush()
    return chunks


def chunk_document(sections: list[ProseSection], rcept_no: str | None) -> list[Chunk]:
    """Turn a document's prose sections into ordered :class:`Chunk` records.

    Each section is split via :func:`split_section`; the document-wide
    ``chunk_index`` runs 0..N-1 across all sections, while ``part_index`` restarts
    at 0 within each section. Empty-content sections (e.g. a title-only TOC header,
    or a shell whose prose lives in nested sub-sections) are skipped and logged --
    there is nothing to embed and we never invent content.

    ``rcept_no`` is the DART receipt number; SEC callers pass ``None`` (SEC filings
    have no rcept_no -- see :class:`Chunk`), and it flows through to the chunk's
    citation anchor unchanged.

    Returns chunks ready for the NEXT step (embedding + ``filing_chunks`` load,
    docs §6); no embedding, DB write, or network call happens here. Pure ->
    unit-tested.
    """
    chunks: list[Chunk] = []
    skipped = 0
    for section in sections:
        if not section.content.strip():
            skipped += 1
            logger.info(
                "chunk_document: skipping empty section (order=%d, title=%r)",
                section.order,
                section.section_title,
            )
            continue
        for part_index, piece in enumerate(split_section(section)):
            chunks.append(
                Chunk(
                    content=piece,
                    chunk_index=len(chunks),  # running document-wide index
                    rcept_no=rcept_no,
                    section_title=section.section_title,
                    section_order=section.order,
                    part_index=part_index,
                )
            )

    _log_distribution(sections, chunks, skipped, rcept_no)
    return chunks


def _log_distribution(
    sections: list[ProseSection],
    chunks: list[Chunk],
    skipped: int,
    rcept_no: str,
) -> None:
    """INFO-log the chunk-length distribution so a human can eyeball the split.

    Emits section/chunk counts plus min/median/max chunk length and how many
    chunks exceeded :data:`TARGET_CHARS` -- enough to see at a glance whether the
    size constants are producing a sane distribution on real filings.
    """
    if not chunks:
        logger.info(
            "chunk_document: rcept_no=%s -> 0 chunks from %d section(s) "
            "(%d empty section(s) skipped)",
            rcept_no,
            len(sections),
            skipped,
        )
        return
    lengths = [len(c.content) for c in chunks]
    over_target = sum(1 for n in lengths if n > TARGET_CHARS)
    logger.info(
        "chunk_document: rcept_no=%s -> %d chunk(s) from %d section(s) "
        "(%d empty skipped); length min/median/max=%d/%d/%d, %d over target(%d)",
        rcept_no,
        len(chunks),
        len(sections),
        skipped,
        min(lengths),
        int(statistics.median(lengths)),
        max(lengths),
        over_target,
        TARGET_CHARS,
    )
