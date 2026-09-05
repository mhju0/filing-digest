"""Index KURE-v1 vectors and publish filing-level search readiness.

The step after ingest writes chunks with
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
   vectors in input order; strict zip checks the entire batch before any write.
   PostgreSQL tests cover alignment, failed batches, and resumable indexing.

Binding ``list[float]`` to the ``vector(1024)`` column goes through the ORM's
:class:`pgvector.sqlalchemy.Vector` type on ``FilingChunk.embedding`` (its bind
processor serializes the list for pgvector), so no manual ``register_vector`` /
cast is needed on this typed-column path.
"""

import argparse
import asyncio
import logging
import uuid

from sqlalchemy import bindparam, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Filing, FilingChunk
from app.db.session import get_async_engine, get_async_session
from app.embeddings.kure import embed_texts

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 32


async def backfill_embeddings(
    session: AsyncSession, batch_size: int = DEFAULT_BATCH_SIZE, limit: int | None = None
) -> int:
    """Resume unpublished filings and recover stale readiness markers.

    ``limit`` caps the number of chunks embedded across all filings.
    """
    statement = (
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
    filing_ids = (await session.execute(statement)).scalars().all()
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
    if batch_size < 1:
        raise ValueError(f"batch size must be >= 1, got {batch_size}")
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
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
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            vectors = await asyncio.to_thread(embed_texts, [row.content for row in batch])
            # Build the whole batch before executing so a length mismatch cannot
            # write a partial set. Core executemany also tolerates chunks removed
            # by concurrent re-ingestion; publication below rechecks readiness.
            parameters = [
                {"chunk_id": row.id, "vector": vector}
                for row, vector in zip(batch, vectors, strict=True)
            ]
            await session.execute(
                FilingChunk.__table__.update()
                .where(FilingChunk.id == bindparam("chunk_id"))
                .values(embedding=bindparam("vector")),
                parameters,
            )
            await session.commit()
            filled += len(batch)

        # Serialize the final counts/publication with the Filing upsert used by
        # re-ingestion. If replacement wins the lock first, these counts see its
        # new NULL vectors; if indexing wins first, replacement subsequently
        # resets indexed_at to NULL before swapping the snapshot.
        await session.execute(
            select(Filing.id).where(Filing.id == filing_id).with_for_update()
        )
        total_chunks, pending_chunks = (
            await session.execute(
                select(
                    func.count(FilingChunk.id),
                    func.count(FilingChunk.id).filter(FilingChunk.embedding.is_(None)),
                ).where(FilingChunk.filing_id == filing_id)
            )
        ).one()
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
