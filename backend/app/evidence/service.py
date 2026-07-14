"""Deterministic resolution of Citations into openable Filing Sources."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.filings import FilingIdentity, RegulatorySource
from app.llm.answer import Answer
from app.schemas import (
    MAX_CITATION_EXCERPT_CHARS,
    Citation,
    CitationAnchor,
    FilingSource,
)


class EvidenceIntegrityError(RuntimeError):
    """A claim cannot resolve through a Filing Chunk to a Filing Source."""


@dataclass(frozen=True)
class EvidenceBundle:
    citations: list[Citation]
    filing_sources: list[FilingSource]


def _filing_identity(filing: Any) -> FilingIdentity:
    source = str(filing.source).strip()
    if source == "dart":
        value = filing.rcept_no
    elif source == "sec":
        value = filing.sec_accession_no
    else:
        raise EvidenceIntegrityError(f"unsupported regulatory source: {source!r}")
    if value is None or not str(value).strip():
        raise EvidenceIntegrityError(
            f"filing {filing.id} has no immutable {source} filing identifier"
        )
    try:
        return FilingIdentity(
            source=RegulatorySource(source), source_filing_id=str(value).strip()
        )
    except ValueError as exc:
        raise EvidenceIntegrityError(str(exc)) from exc


def _is_openable_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def filing_source_from_filing(filing: Any) -> FilingSource:
    """Build a Filing Source from canonical regulator identity and metadata."""

    identity = _filing_identity(filing)
    title = str(filing.title).strip() if filing.title is not None else ""
    url = str(filing.url).strip() if filing.url is not None else ""
    if not title:
        raise EvidenceIntegrityError(f"filing {filing.id} has no title")
    if not _is_openable_url(url):
        raise EvidenceIntegrityError(f"filing {filing.id} has no openable URL")
    filed_at = filing.filed_at.isoformat() if filing.filed_at else None
    return FilingSource(
        id=identity.stable_id,
        source=identity.source.value,
        source_filing_id=identity.source_filing_id,
        title=title,
        url=url,
        filed_at=filed_at,
    )


def _bounded_excerpt(text: Any) -> str:
    excerpt = str(text).strip() if text is not None else ""
    if not excerpt:
        raise EvidenceIntegrityError("cited Filing Chunk has no evidence excerpt")
    return excerpt[:MAX_CITATION_EXCERPT_CHARS]


def resolve_evidence(
    answer: Answer, chunks: Sequence[Any], filings: Sequence[Any]
) -> EvidenceBundle:
    """Resolve an answer in first-citation order through chunks to filings."""

    chunks_by_id = {str(chunk.chunk_id): chunk for chunk in chunks}
    filings_by_id = {filing.id: filing for filing in filings}
    ordered_chunk_ids: list[str] = []
    seen_chunk_ids: set[str] = set()
    for segment in answer.answer_segments:
        if not segment.citations:
            raise EvidenceIntegrityError("answer claim has no Citation")
        for chunk_id in segment.citations:
            normalized = str(chunk_id)
            if normalized not in seen_chunk_ids:
                seen_chunk_ids.add(normalized)
                ordered_chunk_ids.append(normalized)

    citations: list[Citation] = []
    filing_sources: list[FilingSource] = []
    source_ids: set[str] = set()
    for chunk_id in ordered_chunk_ids:
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            raise EvidenceIntegrityError(f"citation references unknown chunk {chunk_id!r}")
        filing = filings_by_id.get(chunk.filing_id)
        if filing is None:
            raise EvidenceIntegrityError(
                f"chunk {chunk_id!r} cannot resolve filing {chunk.filing_id}"
            )
        filing_source = filing_source_from_filing(filing)
        if filing_source.id not in source_ids:
            source_ids.add(filing_source.id)
            filing_sources.append(filing_source)
        citations.append(
            Citation(
                id=chunk_id,
                filing_source_id=filing_source.id,
                excerpt=_bounded_excerpt(chunk.text),
                anchor=CitationAnchor(
                    section_title=chunk.section_title,
                    section_order=chunk.section_order,
                    part_index=chunk.part_index,
                    chunk_index=chunk.chunk_index,
                ),
            )
        )

    return EvidenceBundle(citations=citations, filing_sources=filing_sources)
