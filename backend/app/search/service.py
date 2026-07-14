"""Semantic search over ``filing_chunks`` via KURE-v1 + pgvector cosine distance.

Query-time counterpart to the ingest/embedding pipeline (chunking.py, kure.py,
backfill.py): a query string is embedded with the SAME model and SAME
``normalize_embeddings=True`` invariant as the stored chunk vectors (embed_texts
is reused, not re-implemented), so query and chunk vectors live in the same
space and a plain nearest-neighbor search is meaningful.

Design:

- **pgvector ``<=>`` is cosine DISTANCE** (``1 - cosine_similarity``), not
  similarity. :func:`_distance_to_similarity` converts explicitly so callers
  never have to remember which one a raw DB number is; :class:`SearchResult`
  docs the converted field.
- **Only published indexes are visible.** SQL requires a non-NULL chunk vector
  and the owning filing's non-NULL ``indexed_at``. A partially indexed filing is
  therefore hidden as one unit rather than leaking whichever chunks ran first.
- **``top_k`` is capped** at :data:`MAX_TOP_K` so a caller can't force an
  unbounded table scan/sort.
- **``company_id`` scoping joins through ``filings``** (chunks have no
  ``company_id`` of their own). ``filing_id`` scoping (below) is a direct
  column filter, no join needed. Period/source filters are not part of the
  current API.
- Row -> :class:`SearchResult` assembly is a pure function
  (:func:`_row_to_result`), unit-tested without a DB (implement-step pattern:
  persist.py, chunking.py, kure.py).
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Filing, FilingChunk
from app.embeddings.kure import embed_texts
from app.search.constants import DEFAULT_TOP_K, MAX_TOP_K

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """One semantic search hit -- always citation-grounded (docs §4/§6).

    ``score`` is cosine SIMILARITY (``1 - cosine_distance``), range
    ``[-1, 1]`` in general and effectively ``[0, 1]`` here since both query and
    chunk vectors are unit-normalized (see ``app.embeddings.kure``); higher is
    more similar. The remaining fields mirror ``app.ingest.chunking.Chunk`` /
    ``filing_chunks.meta`` exactly, so a hit is always traceable to its
    source filing/section/paragraph.
    """

    chunk_id: uuid.UUID
    filing_id: uuid.UUID
    text: str
    score: float
    rcept_no: str | None
    section_title: str | None
    section_order: int | None
    part_index: int | None
    chunk_index: int


def clamp_top_k(top_k: int) -> int:
    """Clamp a caller-supplied ``top_k`` to ``[1, MAX_TOP_K]`` (pure).

    Raises ``ValueError`` for a non-positive value -- a caller asking for 0 or
    fewer results almost certainly has a bug, so we refuse rather than
    silently returning ``[]``.
    """
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    return min(top_k, MAX_TOP_K)


def _distance_to_similarity(distance: float) -> float:
    """Convert pgvector cosine DISTANCE to cosine SIMILARITY (pure).

    pgvector's ``<=>`` operator returns ``1 - cosine_similarity`` [Verified:
    pgvector docs, "cosine distance" operator]; this is the one place that
    inversion happens; everything above :func:`search_chunks` deals only in
    similarity.
    """
    return 1.0 - distance


def _row_to_result(row: Any) -> SearchResult:
    """Assemble one DB row (chunk columns + ``meta`` + raw distance) into a
    :class:`SearchResult` (pure).

    ``row.meta`` is the ``filing_chunks.meta`` JSONB written from a normalized
    Filing Chunk: ``{rcept_no, section_title, section_order, part_index}``.
    Missing keys degrade to ``None`` rather than
    raising, since ``meta`` is caller-controlled JSON, not a schema-enforced
    column.
    """
    meta = row.meta or {}
    return SearchResult(
        chunk_id=row.id,
        filing_id=row.filing_id,
        text=row.content,
        score=_distance_to_similarity(row.distance),
        rcept_no=meta.get("rcept_no"),
        section_title=meta.get("section_title"),
        section_order=meta.get("section_order"),
        part_index=meta.get("part_index"),
        chunk_index=row.chunk_index,
    )


async def search_chunks(
    session: AsyncSession,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    company_id: uuid.UUID | None = None,
    filing_id: uuid.UUID | None = None,
) -> list[SearchResult]:
    """Semantic search over ``filing_chunks`` for the ``top_k`` nearest to ``query``.

    ``query`` is embedded via :func:`app.embeddings.kure.embed_texts` -- the
    same model/normalization used to build the stored vectors, so the search is
    a plain cosine nearest-neighbor lookup (``<=>``, ascending distance).
    Chunks with ``embedding IS NULL`` and filings whose index is not yet
    published are excluded. ``top_k`` is clamped via :func:`clamp_top_k`.

    ``company_id``, if given, scopes results to that company's filings (a join
    through ``filings``); a company with no chunks (or an unknown id) yields
    ``[]`` rather than an error. ``filing_id``, if given, additionally scopes
    results to that one filing (a direct column filter -- chunks already carry
    their own ``filing_id``, no join needed); this is what lets a caller (e.g.
    the digest narrative) pin retrieval to one specific filing instead of a
    company's whole chunk corpus. ``None`` (the default for both) preserves the
    exact prior behavior -- ``/search`` never passes ``filing_id``.

    Period and source filters are not part of the current API. ``filings.period``
    and ``filings.source`` are available if that scope is added later.
    """
    k = clamp_top_k(top_k)
    [query_vector] = embed_texts([query])
    distance = FilingChunk.embedding.cosine_distance(query_vector)

    stmt = (
        select(
            FilingChunk.id,
            FilingChunk.filing_id,
            FilingChunk.content,
            FilingChunk.chunk_index,
            FilingChunk.meta,
            distance.label("distance"),
        )
        .join(Filing, Filing.id == FilingChunk.filing_id)
        .where(
            FilingChunk.embedding.is_not(None),
            Filing.indexed_at.is_not(None),
        )
        .order_by(distance)
        .limit(k)
    )
    if company_id is not None:
        stmt = stmt.where(Filing.company_id == company_id)
    if filing_id is not None:
        stmt = stmt.where(FilingChunk.filing_id == filing_id)

    rows = (await session.execute(stmt)).all()
    results = [_row_to_result(row) for row in rows]
    logger.info(
        "search_chunks: query_length=%d top_k=%d company_id=%s filing_id=%s -> %d result(s)",
        len(query),
        k,
        company_id,
        filing_id,
        len(results),
    )
    return results
