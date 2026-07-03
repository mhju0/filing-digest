"""Backfill KURE-v1 vectors into ``filing_chunks`` rows where embedding IS NULL.

The step after ingest (app/ingest/persist.py) writes chunks with
``embedding = NULL``; this module fills those vectors. It is a batch job, not a
request path -- run it once by hand (``python -m app.embeddings.backfill``) or
call :func:`backfill_embeddings` from another async context.

Design:

1. **Only ``embedding IS NULL`` rows are selected**, so the job is idempotent:
   a chunk already embedded is never recomputed, and a re-run picks up exactly
   the rows a prior run left unfilled.

2. **Per-batch commit** (not one big transaction). A mid-run failure keeps every
   batch already written; the next run continues from the remaining NULL rows.
   This composes with (1): interrupt + re-run is safe and does no double work.

3. **Positional id<->vector alignment is guarded.** ``embed_texts`` returns
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
from typing import Sequence, TypeVar

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FilingChunk
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


def align_ids_with_vectors(
    ids: Sequence[_T], vectors: Sequence[_V]
) -> list[tuple[_T, _V]]:
    """Zip chunk ids with their vectors 1:1, guarding alignment (pure).

    ``embed_texts`` returns vectors positionally, so ``ids[i]`` owns
    ``vectors[i]``. A length mismatch means a silent misalignment (every later
    vector lands on the wrong chunk), so we raise instead of zipping to the
    shorter sequence. Separated out and unit-tested without the model or DB.
    """
    if len(ids) != len(vectors):
        raise ValueError(
            f"id/vector count mismatch: {len(ids)} ids vs {len(vectors)} vectors"
        )
    return list(zip(ids, vectors))


async def backfill_embeddings(
    session: AsyncSession, batch_size: int = DEFAULT_BATCH_SIZE, limit: int | None = None
) -> int:
    """Fill ``filing_chunks.embedding`` for every NULL-embedding chunk.

    Selects ``(id, content)`` for chunks with ``embedding IS NULL`` (ordered by
    id for a stable, reproducible pass), embeds them in batches of ``batch_size``,
    and UPDATEs each chunk with its vector. Commits after every batch. Returns the
    number of chunks embedded (0 if none were pending). ``limit`` caps how many
    NULL chunks are processed (useful for a smoke run); ``None`` = all.
    """
    stmt = (
        select(FilingChunk.id, FilingChunk.content)
        .where(FilingChunk.embedding.is_(None))
        .order_by(FilingChunk.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()
    total = len(rows)
    if total == 0:
        logger.info("backfill: no chunks with embedding IS NULL; nothing to do")
        return 0

    logger.info("backfill: %d chunk(s) to embed (batch_size=%d)", total, batch_size)
    filled = 0
    for batch in _batched(rows, batch_size):
        ids = [row.id for row in batch]
        texts = [row.content for row in batch]
        vectors = embed_texts(texts)
        for chunk_id, vector in align_ids_with_vectors(ids, vectors):
            await session.execute(
                update(FilingChunk)
                .where(FilingChunk.id == chunk_id)
                .values(embedding=vector)
            )
        await session.commit()  # per-batch: a later failure preserves this batch
        filled += len(batch)
        logger.info("backfill: %d/%d chunk(s) embedded", filled, total)

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

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(_run(args.batch_size, args.limit))


if __name__ == "__main__":
    main()
