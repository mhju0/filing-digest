"""Deterministic pull of authoritative financial figures from ``financials``.

Symmetric counterpart to the narrative/number guards in :mod:`app.llm`: those
keep numbers OUT of LLM prose; this puts numbers into the answer authoritatively,
straight from the structured filing API (DART/SEC financials) with a citation
anchor and never through the LLM (CLAUDE.md: "숫자는 구조화 filing API에서만 온다").

Design mirrors :func:`app.search.service._row_to_result`: row -> schema shaping
is a pure function (:func:`build_figures`), unit-testable without a DB. The
caller runs the query; this module only shapes already-fetched rows.
"""

import logging
import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Financial
from app.schemas import Figure

logger = logging.getLogger(__name__)


async def fetch_financials(
    session: AsyncSession,
    company_id: uuid.UUID,
    period: str | None = None,
) -> list[Financial]:
    """Read authoritative ``financials`` rows for one company (thin DB read).

    Read-only counterpart to :func:`build_figures`: this runs the query and
    hands back the ORM ``Financial`` entities unchanged; ``build_figures`` shapes
    them into :class:`Figure`. Mirrors :func:`app.search.service.search_chunks`'s
    session/query pattern (session-first positional, ``select`` + ``where``).

    ``company_id`` scopes every read. ``period`` (e.g. a fiscal period label), if
    given, further filters; ``None`` returns the whole company scope. An unknown
    id or empty scope yields ``[]`` rather than raising.

    Returns ORM entities via ``.scalars()`` -- NOT ``Row`` objects and NOT
    converted to any DTO here -- so the ``value`` ``Decimal`` reaches
    :func:`build_figures` with ``numeric(24,4)`` precision intact (never cast to
    ``float``).
    """
    stmt = select(Financial).where(Financial.company_id == company_id)
    if period is not None:
        stmt = stmt.where(Financial.period == period)

    rows = (await session.execute(stmt)).scalars().all()
    logger.info(
        "fetch_financials: company_id=%s period=%s -> %d row(s)",
        company_id,
        period,
        len(rows),
    )
    return list(rows)


class FigureError(RuntimeError):
    """Raised by :func:`build_figures` when a row cannot be a citation anchor.

    Fail-loud sibling of ``app.llm.number_guard.NumberInNarrativeError``: a
    figure with no ``filing_id`` has nothing to cite, and silently passing it
    through would break the "every claim carries a citation" invariant -- so we
    raise rather than emit an un-anchored number.
    """


def build_figures(rows: Iterable[Any]) -> list[Figure]:
    """Shape already-fetched ``financials`` rows into :class:`Figure` (pure).

    Each ``row`` (an ORM ``Financial`` object or any row exposing the same
    attributes) becomes exactly one Figure, carrying its own ``filing_id`` so the
    value is a self-contained citation anchor. ``value`` is passed through as the
    ``Decimal`` it already is -- never cast to ``float`` -- so ``numeric(24,4)``
    precision (e.g. EPS ``2131.0000``) survives intact.

    ``metric`` is passed through raw (snake_case, no display-label mapping).
    Raises :class:`FigureError` if any row's ``filing_id`` is ``None`` (fail loud;
    no un-anchored numbers). An empty ``rows`` yields ``[]``.
    """
    figures: list[Figure] = []
    for row in rows:
        if row.filing_id is None:
            raise FigureError(
                f"figure has no filing_id (metric={row.metric!r}, "
                f"period={row.period!r}): cannot anchor a citation"
            )
        figures.append(
            Figure(
                metric=row.metric,
                value=row.value,
                unit=row.unit,
                currency=row.currency,
                period=row.period,
                fiscal_year=row.fiscal_year,
                fiscal_quarter=row.fiscal_quarter,
                filing_id=row.filing_id,
            )
        )
    logger.info("build_figures: shaped %d figure(s)", len(figures))
    return figures
