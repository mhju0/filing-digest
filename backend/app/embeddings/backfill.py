"""Index KURE-v1 vectors and publish filing-level search readiness.

The step after ingest (app/ingest/persist.py) writes chunks with
``embedding = NULL``; this module fills those vectors. It is a batch job, not a
request path -- run it once by hand (``python -m app.embeddings.backfill``) or
call :func:`backfill_embeddings` from another async context. Ingest callers use
:func:`index_filing_embeddings` so one filing is completed independently.

Design:

1. **Only ``embedding IS NULL`` rows are selected**, so indexing is idempotent:
   a chunk already embedded is never recomputed, and a re-run picks up exactly
   the rows a prior run left unfilled.

2. **Per-batch commit** (not one big transaction). A mid-run failure keeps every
   batch already written; the next run continues from the remaining NULL rows.
   This composes with (1): interrupt + re-run is safe and does no double work.

3. **Readiness is atomic from search's perspective.** Persistence resets the
   target filing's ``indexed_at``. Scoped indexing sets it only after that filing
   has at least one chunk and every chunk has a vector; search requires it.

4. **Positional id<->vector alignment is guarded.** ``embed_texts`` returns
   vectors in input order; :func:`align_ids_with_vectors` refuses to zip when the
   counts differ, so a wrong vector can never be written onto a chunk. The
   alignment is a pure function, unit-tested without the model or DB.

Binding ``list[float]`` to the ``vector(1024)`` column goes through the ORM's
:class:`pgvector.sqlalchemy.Vector` type on ``FilingChunk.embedding`` (its bind
processor serializes the list for pgvector), so no manual ``register_vector`` /
cast is needed on this typed-column path.
"""

import argparse
import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Filing, FilingChunk
from app.db.session import get_async_engine, get_async_session
from app.embeddings.kure import embed_texts

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 32

_T = TypeVar("_T")
_V = TypeVar("_V")


def _batched(items: Sequence[_T], size: int) -> list[Sequence[_T]]:
    """Split ``items`` into consecutive chunks of at most ``size`` (order-kept)."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    return [items[i : i + size] for i in range(0, len(items), size)]


def align_ids_with_vectors(ids: Sequence[_T], vectors: Sequence[_V]) -> list[tuple[_T, _V]]:
    """Zip chunk ids with their vectors 1:1, guarding alignment (pure).

    ``embed_texts`` returns vectors positionally, so ``ids[i]`` owns
    ``vectors[i]``. A length mismatch means a silent misalignment (every later
    vector lands on the wrong chunk), so we raise instead of zipping to the
    shorter sequence. Separated out and unit-tested without the model or DB.
    """
    if len(ids) != len(vectors):
        raise ValueError(f"id/vector count mismatch: {len(ids)} ids vs {len(vectors)} vectors")
    return list(zip(ids, vectors, strict=True))


def _pending_filing_ids_statement():
    """Select filings that are unpublished or contain a pending chunk vector."""
    return (
        select(Filing.id)
        .join(FilingChunk, FilingChunk.filing_id == Filing.id)
        .where(
            or_(
                Filing.indexed_at.is_(None),
                FilingChunk.embedding.is_(None),
            )
        )
        .distinct()
        .order_by(Filing.id)
    )


def _lock_filing_for_publication_statement(filing_id: uuid.UUID):
    """Lock one filing so re-ingestion cannot race readiness publication."""
    return select(Filing.id).where(Filing.id == filing_id).with_for_update()


async def backfill_embeddings(
    session: AsyncSession, batch_size: int = DEFAULT_BATCH_SIZE, limit: int | None = None
) -> int:
    """Resume every filing whose search index is not yet published.

    Enumerates unpublished filings plus any filing containing a NULL vector in
    stable id order, so a stale readiness marker is self-healing. Delegates to
    the scoped indexer and returns the number of chunks embedded. ``limit`` is a
    global cap across filings (useful for a smoke run); ``None`` means all
    pending chunks.
    """
    filing_ids = (
        (
            await session.execute(_pending_filing_ids_statement())
        )
        .scalars()
        .all()
    )
    filled = 0
    for filing_id in filing_ids:
        remaining = None if limit is None else max(limit - filled, 0)
        if remaining == 0:
            break
        filled += await index_filing_embeddings(
            session,
            filing_id,
            batch_size=batch_size,
            limit=remaining,
        )
    if filled == 0:
        logger.info("backfill: no pending Filing Chunks; nothing to do")
    return filled


async def index_filing_embeddings(
    session: AsyncSession,
    filing_id: uuid.UUID,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
) -> int:
    """Index one filing and publish readiness only after every chunk is embedded.

    Readiness is cleared and committed before any batch starts. Successful
    batches then commit independently, so a retry resumes at the remaining NULL
    vectors while search hides the whole filing. ``filings.indexed_at`` is
    republished only when the target filing has at least one chunk and no pending
    chunks.
    """
    await session.execute(
        update(Filing).where(Filing.id == filing_id).values(indexed_at=None)
    )
    await session.commit()

    stmt = (
        select(FilingChunk.id, FilingChunk.content)
        .where(
            FilingChunk.filing_id == filing_id,
            FilingChunk.embedding.is_(None),
        )
        .order_by(FilingChunk.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).all()
    filled = 0
    try:
        for batch in _batched(rows, batch_size):
            ids = [row.id for row in batch]
            vectors = embed_texts([row.content for row in batch])
            for chunk_id, vector in align_ids_with_vectors(ids, vectors):
                await session.execute(
                    update(FilingChunk).where(FilingChunk.id == chunk_id).values(embedding=vector)
                )
            await session.commit()
            filled += len(batch)

        # Serialize the final counts/publication with the Filing upsert used by
        # re-ingestion. If replacement wins the lock first, these counts see its
        # new NULL vectors; if indexing wins first, replacement subsequently
        # resets indexed_at to NULL before swapping the snapshot.
        await session.execute(_lock_filing_for_publication_statement(filing_id))
        total_chunks = (
            await session.execute(
                select(func.count(FilingChunk.id)).where(FilingChunk.filing_id == filing_id)
            )
        ).scalar_one()
        pending_chunks = (
            await session.execute(
                select(func.count(FilingChunk.id)).where(
                    FilingChunk.filing_id == filing_id,
                    FilingChunk.embedding.is_(None),
                )
            )
        ).scalar_one()
        if total_chunks > 0 and pending_chunks == 0:
            await session.execute(
                update(Filing).where(Filing.id == filing_id).values(indexed_at=func.now())
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "indexed filing=%s: embedded=%d total=%d pending=%d",
        filing_id,
        filled,
        total_chunks,
        pending_chunks,
    )
    return filled


async def _run(batch_size: int, limit: int | None) -> int:
    """CLI async body: open one session, backfill, dispose the engine."""
    async with get_async_session() as session:
        count = await backfill_embeddings(session, batch_size=batch_size, limit=limit)
    await get_async_engine().dispose()
    logger.info("backfill complete: %d chunk(s) embedded", count)
    return count


def main() -> None:
    """Entry point: ``python -m app.embeddings.backfill [--batch-size N] [--limit N]``."""
    parser = argparse.ArgumentParser(
        description="Backfill KURE-v1 embeddings into filing_chunks (embedding IS NULL)."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"chunks encoded per batch/commit (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="max NULL-embedding chunks to process (default: all)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(args.batch_size, args.limit))


if __name__ == "__main__":
    main()
