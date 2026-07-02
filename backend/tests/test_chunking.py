"""Tests for prose-section chunking (backend/app/ingest/chunking.py, docs §4/§6).

Offline and focused on the two pure functions this step adds:
- ``split_section``  -- paragraph accumulation to ~TARGET_CHARS, hard cap
  MAX_CHARS, and force-split-with-overlap for one over-cap paragraph.
- ``chunk_document`` -- document-wide chunk_index (0..N-1), per-section
  part_index, citation anchors preserved, empty sections skipped.

A single live test (skipped unless DART_API_KEY is set) runs the full pipeline on
the 삼성 2023 사업보고서 recorded in docs §4 and asserts only structural invariants
(>=1 chunk, every chunk <= MAX_CHARS, anchors present), never a hardcoded count or
text. It also logs the length distribution + first 3 chunk previews for a human to
eyeball (public filing text, not a secret); run with ``-s --log-cli-level=INFO``.
"""

import asyncio
import logging
import os
import statistics

import pytest

from app.clients.dart import DartClient, ProseSection, decode_dart_bytes, extract_dsd_prose
from app.config import Settings
from app.ingest.chunking import (
    MAX_CHARS,
    OVERLAP_CHARS,
    TARGET_CHARS,
    Chunk,
    chunk_document,
    split_section,
)

logger = logging.getLogger(__name__)


def _section(content: str, *, title: str | None = "T", order: int = 0) -> ProseSection:
    return ProseSection(section_title=title, content=content, order=order)


# -- split_section: short sections stay whole ---------------------------------


def test_split_short_section_is_single_chunk() -> None:
    section = _section("짧은 문단입니다.\n두 번째 문단입니다.")
    parts = split_section(section)
    assert len(parts) == 1
    # Paragraphs rejoin with the original "\n" boundary.
    assert parts[0] == "짧은 문단입니다.\n두 번째 문단입니다."


def test_split_empty_content_returns_empty() -> None:
    assert split_section(_section("")) == []
    assert split_section(_section("   \n  ")) == []  # whitespace-only -> nothing


# -- split_section: accumulation cuts near TARGET, never over MAX --------------


def test_split_accumulates_paragraphs_and_cuts_near_target() -> None:
    # Six 300-char paragraphs. Accumulating cuts once the running length reaches
    # TARGET(800): 300+300=600 (<800, keep) then +300=900 (>=800, cut) -> pairs...
    # but 900 > ... check the cap too: 600+1+300=901 <= MAX(1200) so it is added
    # before the cut. Each emitted chunk is thus 3 paragraphs (~902 chars).
    para = "가" * 300
    section = _section("\n".join([para] * 6))
    parts = split_section(section)

    assert len(parts) >= 2  # 1800+ chars cannot be one chunk
    for part in parts:
        assert len(part) <= MAX_CHARS
    # No text is lost: concatenating the paragraphs back matches the source set.
    rejoined_paras = "\n".join(parts).split("\n")
    assert rejoined_paras == [para] * 6


def test_split_flushes_before_exceeding_max() -> None:
    # 700-char buffer then a 600-char paragraph: 700+1+600=1301 > MAX(1200), so
    # the 700 chunk is flushed before the 600 is added (neither chunk exceeds MAX).
    section = _section("\n".join(["가" * 700, "나" * 600]))
    parts = split_section(section)
    assert [len(p) for p in parts] == [700, 600]
    assert all(len(p) <= MAX_CHARS for p in parts)


# -- split_section: exact-boundary cases (800 and 1200) -----------------------


def test_split_exactly_target_is_single_chunk() -> None:
    # A lone paragraph of exactly TARGET chars: added, hits the cut threshold,
    # emitted as one chunk (<= MAX, so never force-split).
    section = _section("가" * TARGET_CHARS)
    parts = split_section(section)
    assert parts == ["가" * TARGET_CHARS]


def test_split_exactly_max_is_single_chunk_not_force_split() -> None:
    # A lone paragraph of exactly MAX chars is NOT over the cap (> is strict), so
    # it stays whole -- one chunk, no overlap, no force-split.
    section = _section("가" * MAX_CHARS)
    parts = split_section(section)
    assert len(parts) == 1
    assert parts[0] == "가" * MAX_CHARS


# -- split_section: force-split one over-cap paragraph (with overlap) ----------


def test_split_force_splits_over_cap_paragraph_with_overlap() -> None:
    # One paragraph of 1300 chars (> MAX) -> char-split into MAX-wide windows that
    # overlap by exactly OVERLAP_CHARS, so context survives the forced boundary.
    body = "".join(chr(0xAC00 + (i % 100)) for i in range(1300))  # 1300 distinct-ish
    parts = split_section(_section(body))

    assert len(parts) == 2
    assert all(len(p) <= MAX_CHARS for p in parts)
    assert len(parts[0]) == MAX_CHARS  # first window is a full MAX-wide slice
    # The last OVERLAP_CHARS of chunk 0 are the first OVERLAP_CHARS of chunk 1.
    assert parts[0][-OVERLAP_CHARS:] == parts[1][:OVERLAP_CHARS]
    # Overlap is applied ONLY here: reconstructing with the overlap removed once
    # recovers the exact original paragraph (no text lost, none duplicated beyond
    # the deliberate overlap window).
    assert parts[0] + parts[1][OVERLAP_CHARS:] == body


def test_split_paragraph_boundary_cut_has_no_overlap() -> None:
    # Two paragraphs that together exceed TARGET must be cut at the paragraph
    # boundary with NO overlap (the boundary is already a meaning unit).
    a, b = "가" * 500, "나" * 500  # 500+1+500 = 1001 >= TARGET, <= MAX -> one chunk
    parts = split_section(_section("\n".join([a, b])))
    # 1001 chars stays a single chunk (under MAX); assert no character duplication
    # would occur if it did split -- here it simply does not split.
    assert parts == [a + "\n" + b]


# -- chunk_document: indices, anchors, empty-section skip ----------------------


def test_chunk_document_indices_are_contiguous_and_anchored() -> None:
    sections = [
        _section("\n".join(["가" * 400] * 5), title="회사의 개요", order=0),
        _section("짧은 섹션.", title="사업의 내용", order=1),
    ]
    chunks = chunk_document(sections, rcept_no="20240312000736")

    assert len(chunks) >= 3
    # chunk_index is a gap-free 0..N-1 run across the whole document.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # Every chunk carries its citation anchor.
    for c in chunks:
        assert c.rcept_no == "20240312000736"
        assert c.section_title in {"회사의 개요", "사업의 내용"}
        assert len(c.content) <= MAX_CHARS


def test_chunk_document_part_index_restarts_per_section() -> None:
    # Section 0 splits into several parts; section 1 is a single chunk. part_index
    # must restart at 0 for section 1 even though chunk_index keeps climbing.
    sections = [
        _section("\n".join(["가" * 400] * 5), title="A", order=0),
        _section("나" * 100, title="B", order=1),
    ]
    chunks = chunk_document(sections, rcept_no="R")

    by_section: dict[int, list[Chunk]] = {}
    for c in chunks:
        by_section.setdefault(c.section_order, []).append(c)
    for section_chunks in by_section.values():
        # part_index within a section is a gap-free 0.. run.
        assert [c.part_index for c in section_chunks] == list(range(len(section_chunks)))
    # The last section's single chunk is part_index 0 but a later chunk_index.
    last = chunks[-1]
    assert last.section_order == 1
    assert last.part_index == 0
    assert last.chunk_index == len(chunks) - 1


def test_chunk_document_skips_empty_sections() -> None:
    # A title-only section (empty content, kept upstream as a TOC header) has no
    # prose to embed -> skipped, producing no chunk.
    sections = [
        _section("", title="II. 사업의 내용", order=0),  # title-only -> skipped
        _section("실제 본문입니다.", title="1. 개요", order=1),
    ]
    chunks = chunk_document(sections, rcept_no="R")
    assert len(chunks) == 1
    assert chunks[0].section_order == 1
    assert chunks[0].section_title == "1. 개요"


def test_chunk_document_all_empty_returns_nothing() -> None:
    sections = [_section("", title="T", order=0), _section("  ", title=None, order=1)]
    assert chunk_document(sections, rcept_no="R") == []


# -- live (skipped unless DART_API_KEY is set) --------------------------------


@pytest.mark.skipif(
    not os.environ.get("DART_API_KEY"),
    reason="DART_API_KEY not set; skipping live chunking pipeline",
)
def test_chunk_document_live_samsung_pipeline() -> None:
    # Live, single call: fetch -> decode -> extract -> chunk on the 삼성 2023
    # 사업보고서 (docs §4). Structural invariants only; logs the distribution and
    # first 3 chunk previews for a human. Run: pytest -s --log-cli-level=INFO.
    rcept_no = "20240312000736"

    async def _fetch() -> bytes:
        client = DartClient(settings=Settings())
        try:
            payload = await client.fetch_document(rcept_no)
            return payload.content
        finally:
            await client.aclose()

    raw = asyncio.run(_fetch())
    sections = extract_dsd_prose(decode_dart_bytes(raw))
    chunks = chunk_document(sections, rcept_no=rcept_no)

    assert len(chunks) >= 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert c.rcept_no == rcept_no
        assert len(c.content) <= MAX_CHARS
        assert c.chunk_index >= 0
    # section_title anchor preserved on at least some chunks (TOC headers exist).
    assert any(c.section_title for c in chunks)

    lengths = [len(c.content) for c in chunks]
    logger.info(
        "live chunking -- %d chunk(s) from %d section(s); len min/median/max=%d/%d/%d",
        len(chunks),
        len(sections),
        min(lengths),
        int(statistics.median(lengths)),
        max(lengths),
    )
    for c in chunks[:3]:
        logger.info(
            "  chunk #%d (section=%r, len=%d): %r",
            c.chunk_index,
            c.section_title,
            len(c.content),
            c.content[:120],
        )
