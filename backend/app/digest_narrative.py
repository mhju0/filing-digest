"""Business-overview digest narrative: prose-only KO/EN summary for /digest.

Companion to :mod:`app.llm.narrative` (which powers /answer). Where that module
narrates an arbitrary user question over injected chunks, this one builds a
FIXED, query-less business-overview summary for a company's filing:

- RETRIEVAL: a fixed Korean "business overview" query is run through the SAME
  semantic retrieval used by /answer (:func:`app.search.service.search_chunks`),
  scoped to the company. There is no user query -- the digest summary is a
  stable, company-level artifact.
- ONE LLM CALL returning a flat ``{summary_ko, summary_en}`` JSON. The schema is
  hand-authored (no ``$ref``/``$defs``, one level of nesting) for the same
  reason as :func:`app.llm.answer.build_answer_json_schema`: Solar rejects
  ref/recursive schemas.
- NUMBER POLICY (CLAUDE.md "수치 환각은 절대 금지"): the prompt forbids ALL numerals
  in the prose, and :func:`_assert_summary_number_free` enforces it on BOTH the
  Korean AND English summary in two layers -- the shared financial-number guard
  (:func:`app.llm.number_guard.assert_number_free`, suffix-anchored on 원/%/배)
  reused verbatim, PLUS a stricter bare-digit scan. Stored chunk text keeps
  innocent counts/years, but a GENERATED summary must be fully qualitative
  (numbers live only in the figures track), so ANY numeral -- a bare count like
  "232 subsidiaries", a year, or an English "$5 billion" that the suffix-anchored
  guard alone would miss -- is a violation. On a guard trip we retry ONCE with a
  stronger no-numbers instruction; a second trip yields null summaries (the
  digest still returns its authoritative figures -- the summary is an
  enhancement, never a source of numbers).
- CACHE: a process-local dict keyed by ``filing_id`` memoizes SUCCESSFUL
  summaries (no TTL, no new deps). Guard-blocked / failed generations are NOT
  cached, so a later request can retry.

Unlike /answer's narrative, a digest summary carries NO per-segment citations
(the digest already exposes a filing-level citation card), so none of the
positional-label / citation-guard machinery applies here.
"""

import logging
import re
import unicodedata
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.answer import Answer, AnswerSegment
from app.llm.base import ChatMessage, LLMClient
from app.llm.number_guard import (
    NumberInNarrativeError,
    Violation,
    assert_number_free,
)
from app.search.service import SearchResult, search_chunks

logger = logging.getLogger(__name__)

# Fixed, query-less retrieval seed for the business-overview digest. Deliberately
# generic KO phrasing so it ranks the "회사의 개요 / 사업의 개요 / 주요 제품" sections
# highest for a DART business report; the digest is a company-level artifact, not
# a response to any user question.
_OVERVIEW_QUERY = "회사의 사업 개요와 주요 사업 내용, 주요 제품 및 서비스"
_DIGEST_TOP_K = 6

# Summaries are short (a few sentences x2 languages); cap generation modestly.
_MAX_TOKENS = 512

# Generated digest prose must be fully qualitative -- numbers come only from the
# structured figures track (CLAUDE.md). Any digit (after NFKC normalization) is a
# violation, catching bare counts/years/English currency the suffix-anchored
# financial guard cannot.
_ANY_DIGIT_RE = re.compile(r"\d")

# Process-local memo of SUCCESSFUL summaries, keyed by filing_id (CLAUDE.md: no
# new deps -- a plain dict, no TTL). Guard-blocked/failed generations are never
# stored so a later request gets a fresh attempt.
_SUMMARY_CACHE: dict[uuid.UUID, tuple[str, str]] = {}


class DigestNarrativeError(RuntimeError):
    """Raised when the LLM response cannot be parsed into a :class:`DigestSummary`."""


class DigestSummary(BaseModel):
    """The flat KO/EN business-overview summary the LLM returns."""

    summary_ko: str
    summary_en: str


def build_digest_json_schema() -> dict[str, Any]:
    """Build the Solar ``response_format`` dict for :class:`DigestSummary`.

    Hand-written (not ``DigestSummary.model_json_schema()``) for the same reason
    as :func:`app.llm.answer.build_answer_json_schema`: Solar rejects
    ``$ref``/``$defs`` schemas. This one is flat -- a single object with two
    string fields, one level of nesting.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "digest_summary",
            "schema": {
                "type": "object",
                "properties": {
                    "summary_ko": {"type": "string"},
                    "summary_en": {"type": "string"},
                },
                "required": ["summary_ko", "summary_en"],
                "additionalProperties": False,
            },
        },
    }


_SYSTEM_PROMPT = (
    "You are a filings analyst writing a company's business-overview digest. "
    "Write a concise 2-4 sentence QUALITATIVE overview of the company's business, "
    "main products and services, and operations, in BOTH Korean (summary_ko) and "
    "English (summary_en), each conveying the same substance in its own language, "
    "using ONLY information supported by the source chunks the user provides.\n"
    "HARD CONSTRAINT -- ZERO NUMBERS: the prose must contain NO digit and NO "
    "numeral at all, in either language. This is absolute. Never write a founding "
    "year, a date, a count of ANYTHING (subsidiaries, affiliates, employees, "
    "divisions, regions, offices, products), an amount, a percentage, or any "
    "figure -- all numbers come from a separate structured data track, never from "
    "you. Replace every number with a qualitative phrase or drop it: e.g. "
    "'founded in 1969' -> 'a long-established company' (or omit), '232 "
    "subsidiaries' -> 'numerous subsidiaries', 'nine regional offices' -> "
    "'multiple regional offices', 'two divisions' -> 'its main divisions', 'grew "
    "10%' -> 'grew'. Before answering, re-read BOTH summaries and remove any "
    "remaining digit. Do not write citations or bracketed labels.\n"
    "Respond with JSON matching the required schema: an object with 'summary_ko' "
    "and 'summary_en' string fields."
)

_RETRY_REINFORCEMENT = (
    "Your previous response contained a digit, which is strictly forbidden. "
    "Rewrite BOTH summaries with absolutely NO digit or numeral of any kind -- "
    "no counts, quantities, amounts, percentages, years, or dates. Replace every "
    "number with a qualitative phrase (e.g. 'numerous', 'several', 'many') or "
    "omit it entirely."
)


def _format_sources(chunks: list[SearchResult]) -> str:
    """Render retrieved chunks as plain source blocks for the prompt.

    No positional labels (the digest summary carries no per-segment citations),
    so nothing tempts the model to emit bracketed labels alongside the prose.
    """
    return "\n\n".join(f"- {chunk.text}" for chunk in chunks)


def _build_messages(
    chunks: list[SearchResult], *, reinforce: bool
) -> list[ChatMessage]:
    """Assemble the chat messages; ``reinforce`` appends the stronger retry rule."""
    messages: list[ChatMessage] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if reinforce:
        messages.append({"role": "system", "content": _RETRY_REINFORCEMENT})
    messages.append(
        {"role": "user", "content": f"Sources:\n{_format_sources(chunks)}"}
    )
    return messages


def _assert_summary_number_free(summary: DigestSummary) -> None:
    """Reject any inline number in EITHER the KO or EN summary (two layers).

    1. The shared financial-number guard
       (:func:`app.llm.number_guard.assert_number_free`) is reused verbatim by
       wrapping each summary as one throwaway :class:`Answer` segment (citations
       empty -- the guard never scans them). It flags currency/percent/multiple
       tokens (suffix-anchored on 원/%/배).
    2. A stricter bare-digit scan then rejects ANY remaining numeral. Generated
       digest prose must be fully qualitative (numbers live only in the figures
       track), so bare counts ("232 subsidiaries"), years, and English currency
       ("$5 billion") -- which layer 1 cannot see -- are violations too.

    Raises :class:`app.llm.number_guard.NumberInNarrativeError` on the first
    violation of either layer.
    """
    probe = Answer(
        answer_segments=[
            AnswerSegment(text=summary.summary_ko, citations=[]),
            AnswerSegment(text=summary.summary_en, citations=[]),
        ]
    )
    assert_number_free(probe)

    for index, text in enumerate((summary.summary_ko, summary.summary_en)):
        match = _ANY_DIGIT_RE.search(unicodedata.normalize("NFKC", text))
        if match is not None:
            raise NumberInNarrativeError(
                [Violation(index, match.group(), match.span())]
            )


async def _call_and_guard(
    client: LLMClient, messages: list[ChatMessage]
) -> DigestSummary:
    """One Solar call -> parse -> number-guard both summaries.

    Raises :class:`DigestNarrativeError` on an unparseable body and
    :class:`app.llm.number_guard.NumberInNarrativeError` when the guard trips.
    """
    result = await client.complete(
        messages,
        response_format=build_digest_json_schema(),
        max_tokens=_MAX_TOKENS,
    )
    try:
        summary = DigestSummary.model_validate_json(result.text)
    except ValueError as exc:
        raise DigestNarrativeError(
            "LLM response was not valid digest-summary JSON"
        ) from exc
    _assert_summary_number_free(summary)
    return summary


async def _generate_summary(
    client: LLMClient, chunks: list[SearchResult]
) -> DigestSummary | None:
    """One attempt + one number-guard retry; ``None`` if the guard blocks twice."""
    try:
        return await _call_and_guard(
            client, _build_messages(chunks, reinforce=False)
        )
    except NumberInNarrativeError:
        logger.warning(
            "digest number guard blocked; retrying with a stronger no-numbers instruction"
        )
    try:
        return await _call_and_guard(
            client, _build_messages(chunks, reinforce=True)
        )
    except NumberInNarrativeError:
        logger.warning(
            "digest number guard blocked after retry; returning null summaries"
        )
        return None


async def build_company_summary(
    session: AsyncSession,
    client: LLMClient,
    company_id: uuid.UUID,
    filing_id: uuid.UUID | None = None,
) -> tuple[str | None, str | None]:
    """Build (or reuse a cached) KO/EN business-overview summary for a company.

    Retrieves business-overview chunks via the shared semantic search (fixed
    query, no user input), then generates a guarded prose summary. Returns
    ``(None, None)`` when there are no chunks to retrieve or the number guard
    blocks the prose twice -- the caller keeps its authoritative figures either
    way. Successful summaries are memoized by ``filing_id``.

    ``filing_id``, when given by the caller (the /digest route's deterministic
    "latest filing" -- see ``routes.py``'s ``target_period`` selection), scopes
    retrieval to that ONE filing via :func:`app.search.service.search_chunks`'s
    ``filing_id`` parameter, so a multi-filing company's summary is always
    generated from its intended filing rather than whichever chunk search
    happens to rank first. ``None`` (e.g. no financials rows to derive a filing
    from) falls back to the prior company-wide retrieval.

    May raise :class:`DigestNarrativeError` (unparseable body) or Solar/httpx
    transport errors -- the /digest route catches those and falls back to null
    summaries so a summary failure never breaks the figures response. Retrieval
    (``search_chunks``) errors are NOT swallowed here (shared infra, same as
    /search and /answer).
    """
    chunks = await search_chunks(
        session,
        query=_OVERVIEW_QUERY,
        top_k=_DIGEST_TOP_K,
        company_id=company_id,
        filing_id=filing_id,
    )
    if not chunks:
        return (None, None)

    # Cache key: the caller-provided (deterministic) filing_id when given,
    # else the top-retrieved chunk's filing (prior company-wide fallback).
    cache_key = filing_id if filing_id is not None else chunks[0].filing_id
    cached = _SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        logger.info("digest summary cache hit for filing_id=%s", cache_key)
        return cached

    summary = await _generate_summary(client, chunks)
    if summary is None:
        return (None, None)

    result = (summary.summary_ko, summary.summary_en)
    _SUMMARY_CACHE[cache_key] = result
    logger.info(
        "digest summary generated and cached for filing_id=%s (%d source chunk(s))",
        cache_key,
        len(chunks),
    )
    return result


def clear_summary_cache() -> None:
    """Drop all memoized digest summaries (test hook / future cache-bust)."""
    _SUMMARY_CACHE.clear()
