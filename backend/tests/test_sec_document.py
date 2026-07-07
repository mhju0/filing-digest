"""Tests for SEC EDGAR archive document fetch + 10-K prose extraction.

Two offline concerns, no network call:
- ``SecClient.fetch_document`` (``sec.py``, fetch-only) -- ``_archive_url`` (pure)
  and the fetch path via ``httpx.MockTransport``. The archive URL shape is
  [Inferred] -- not verified against a live fetch in this offline step.
- ``extract_10k_prose`` (``sec_document.py``) -- Item 1 (Business) / Item 7 (MD&A)
  prose extraction from a trimmed fixture that mimics real 10-K structure (TOC
  links, real body headings, forward/backward cross-references, inline-XBRL noise
  tags). Fail-loud paths (missing item, too-short) are exercised too. The real
  Apple 10-K markup is [Unknown] here; the live gate parses the real ~1.5MB doc.
"""

import asyncio
import logging

import httpx
import pytest

from app.clients.sec import SecClient, _archive_url
from app.clients.sec_document import (
    SecDocumentParseError,
    _html_to_text,
    extract_10k_prose,
)
from app.config import Settings

logger = logging.getLogger(__name__)

_FAKE_SETTINGS = Settings(sec_user_agent="filing-digest-test test@example.com")


def test_archive_url_strips_dashes_and_leading_zeros() -> None:
    url = _archive_url("0000320193", "0000320193-23-000106", "aapl-20230930.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019323000106/aapl-20230930.htm"
    )


def test_fetch_document_offline_builds_url_and_sends_user_agent() -> None:
    cik = 320193
    accession_number = "0000320193-23-000106"
    primary_document = "aapl-20230930.htm"
    body = b"<html>filing body</html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019323000106/aapl-20230930.htm"
        )
        assert request.headers["User-Agent"] == _FAKE_SETTINGS.sec_user_agent
        return httpx.Response(200, content=body)

    async def _run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sec = SecClient(settings=_FAKE_SETTINGS, client=client)
        try:
            return await sec.fetch_document(cik, accession_number, primary_document)
        finally:
            await client.aclose()

    payload = asyncio.run(_run())
    assert payload.raw_bytes == body
    assert payload.cik == "0000320193"
    assert payload.accession_number == accession_number
    assert payload.primary_document == primary_document
    assert payload.url.endswith("/aapl-20230930.htm")


# -- extract_10k_prose: trimmed 10-K fixture ----------------------------------
#
# The fixture mimics the three ways "Item 1"/"Item 7" appear in a real 10-K so
# the heuristic can be proven to pick only the body heading:
#   1. TABLE OF CONTENTS -- short <a> links "Item 1. Business", "Item 7. MD&A",
#      each next to a page number. These must NOT be mistaken for the body.
#   2. BODY HEADINGS -- the real "Item 1." (with an &#160; nbsp, proving entity
#      decoding + space folding) and "Item 7." headings, each followed by KB of
#      prose bounded by "Item 1A"/"Item 7A".
#   3. CROSS-REFERENCES -- a forward ref "see Item 7. Management's Discussion
#      below" placed inside Item 1 *before* the real Item 7 heading (would fool a
#      naive longest-span search), and a backward ref "Item 1. Business" inside
#      Item 7 (has no Item 1A after it). Both must be ignored.
# Inline-XBRL noise: an <ix:header><ix:hidden> block with a stray number that
# must never leak into prose, and an inline <ix:nonNumeric> whose *visible* text
# must be kept.

# Sanity floor for "the body region is clearly bigger than a TOC stub"; distinct
# from the production 500-char fail-loud threshold so the intent reads clearly.
_MIN_LEN_SANITY = 300

# Unique sentinels let each assertion pin content to a specific source region.
_BIZ_SENTINEL = "BUSINESSBODYSENTINEL"
_BIZ_TAIL_SENTINEL = "BUSINESSTAILSENTINEL"  # sits AFTER the forward cross-ref
_RISK_SENTINEL = "RISKFACTORSSENTINEL"
_MDNA_SENTINEL = "MDNABODYSENTINEL"
_MDNA_TAIL_SENTINEL = "MDNATAILSENTINEL"  # sits AFTER the financial table
_MKT_SENTINEL = "MARKETRISKSENTINEL"
_HIDDEN_NOISE = "999888777666"

# Realistic MD&A segment-revenue table numbers -- these must never survive into
# extracted prose (this is the live-verified bug: raw table digits leaking into
# a chunk, then correctly blocked downstream by number_guard on contaminated
# input). "(7) %" / "14 %" mirror the real leaked chunk's percent-change cells.
_TABLE_PRODUCTS_NET = "309,845"
_TABLE_PRODUCTS_PRIOR = "333,845"
_TABLE_SERVICES_NET = "109,158"
_TABLE_SERVICES_PRIOR = "96,169"
_TABLE_PRODUCTS_PCT = "(7) %"
_TABLE_SERVICES_PCT = "14 %"

# Filler long enough that each item body clears the 500-char fail-loud floor.
_FILLER = (
    "The Company designs, manufactures, and markets a broad range of products "
    "and related services across several geographic segments, and this narrative "
    "paragraph exists purely to carry the section comfortably past the minimum "
    "length threshold so that the fail-loud short-text guard does not trip. "
)


def _build_10k_html(
    *, include_item7: bool = True, short_business: bool = False
) -> bytes:
    """Assemble a trimmed 10-K HTML document as UTF-8 bytes.

    ``include_item7=False`` drops both the TOC entries and the Part II body for
    Item 7/7A (so Item 7 is unlocatable). ``short_business=True`` truncates the
    Item 1 body under the 500-char floor (so Item 1 trips the short-text guard).
    """
    business_body = (
        f"{_BIZ_SENTINEL} the Company sells devices and services."
        if short_business
        else f"{_BIZ_SENTINEL} {_FILLER} {_FILLER}"
    )

    toc_rows = [
        '<tr><td><a href="#i1">Item 1. Business</a></td><td>1</td></tr>',
        '<tr><td><a href="#i1a">Item 1A. Risk Factors</a></td><td>6</td></tr>',
        '<tr><td><a href="#i2">Item 2. Properties</a></td><td>18</td></tr>',
    ]
    if include_item7:
        toc_rows += [
            '<tr><td><a href="#i7">Item 7. Management&#8217;s Discussion and '
            "Analysis of Financial Condition and Results of Operations</a></td>"
            "<td>25</td></tr>",
            '<tr><td><a href="#i7a">Item 7A. Quantitative and Qualitative '
            "Disclosures About Market Risk</a></td><td>40</td></tr>",
        ]
    toc_rows.append(
        '<tr><td><a href="#i8">Item 8. Financial Statements</a></td><td>42</td></tr>'
    )

    # Part I -- real Item 1 heading uses &#160; (nbsp) between "Item" and "1".
    part1 = [
        "<p><b>Item&#160;1.</b> Business</p>",
        f"<p>{business_body}</p>",
        '<p><ix:nonNumeric name="dei:EntityRegistrantName">Apple Inc.</ix:nonNumeric>'
        " is the registrant and continues to invest in research and development.</p>",
        # Forward cross-reference to Item 7, BEFORE the real Item 7 heading.
        "<p>For a discussion of results, see Item 7. Management&#8217;s Discussion "
        "and Analysis below.</p>",
        f"<p>{_BIZ_TAIL_SENTINEL} closes the business section discussion.</p>",
        "<p><b>Item 1A.</b> Risk Factors</p>",
        f"<p>{_RISK_SENTINEL} {_FILLER}</p>",
        "<p><b>Item 2.</b> Properties</p>",
        "<p>The Company's headquarters are located in Cupertino, California.</p>",
    ]

    # Part II -- real Item 7 heading, MD&A body, then Item 7A boundary.
    part2 = []
    if include_item7:
        part2 = [
            "<p><b>Item 7.</b> Management&#8217;s Discussion and Analysis of "
            "Financial Condition and Results of Operations</p>",
            f"<p>{_MDNA_SENTINEL} {_FILLER} {_FILLER}</p>",
            "<p>The following table summarizes net sales by reportable segment "
            "for fiscal 2023 and 2022 (in millions, except percentages):</p>",
            "<table><thead><tr><th>Segment</th><th>2023</th><th>% Change</th>"
            "<th>2022</th></tr></thead><tbody>"
            f"<tr><td>Products</td><td>{_TABLE_PRODUCTS_NET}</td>"
            f"<td>{_TABLE_PRODUCTS_PCT}</td><td>{_TABLE_PRODUCTS_PRIOR}</td></tr>"
            f"<tr><td>Services</td><td>{_TABLE_SERVICES_NET}</td>"
            f"<td>{_TABLE_SERVICES_PCT}</td><td>{_TABLE_SERVICES_PRIOR}</td></tr>"
            "</tbody></table>",
            f"<p>{_MDNA_TAIL_SENTINEL} follows the segment table with further "
            "narrative discussion of the drivers behind the change.</p>",
            # Backward cross-reference to Item 1 (period + title), inside Item 7.
            "<p>As described in Part I, Item 1. Business, of this report, demand "
            "remained strong across product categories.</p>",
            "<p><b>Item 7A.</b> Quantitative and Qualitative Disclosures About "
            "Market Risk</p>",
            f"<p>{_MKT_SENTINEL} {_FILLER}</p>",
        ]
    part2.append("<p><b>Item 8.</b> Financial Statements and Supplementary Data</p>")
    part2.append("<p>See the accompanying consolidated financial statements.</p>")

    html = (
        '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
        "<head><title>Form 10-K</title>"
        "<style>.hidden{display:none}</style></head>"
        "<body>"
        "<ix:header><ix:hidden>"
        f'<ix:nonNumeric name="dei:Junk">{_HIDDEN_NOISE}</ix:nonNumeric>'
        "</ix:hidden></ix:header>"
        "<div>Table of Contents</div>"
        "<table>" + "".join(toc_rows) + "</table>"
        + "".join(part1)
        + "".join(part2)
        + "</body></html>"
    )
    return html.encode("utf-8")


def test_extract_10k_returns_two_ordered_prose_sections() -> None:
    sections = extract_10k_prose(_build_10k_html())
    assert len(sections) == 2
    item1, item7 = sections
    # Canonical titles + gap-free document order (0, 1).
    assert item1.section_title == "Item 1. Business"
    assert item1.order == 0
    assert item7.section_title == "Item 7. Management's Discussion and Analysis"
    assert item7.order == 1


def test_extract_10k_item1_body_and_boundary() -> None:
    item1 = extract_10k_prose(_build_10k_html())[0]
    # Real body heading matched despite the &#160; nbsp; content leads with it.
    assert item1.content.startswith("Item 1.")
    # Business prose present, including the tail after the forward cross-ref...
    assert _BIZ_SENTINEL in item1.content
    assert _BIZ_TAIL_SENTINEL in item1.content
    # ...but the section STOPS at Item 1A -- no Risk Factors / MD&A text leaks in.
    assert _RISK_SENTINEL not in item1.content
    assert _MDNA_SENTINEL not in item1.content


def test_extract_10k_item7_body_and_boundary() -> None:
    item7 = extract_10k_prose(_build_10k_html())[1]
    assert item7.content.startswith("Item 7.")
    assert _MDNA_SENTINEL in item7.content
    # Ends at Item 7A -- Market Risk / Item 8 text excluded.
    assert _MKT_SENTINEL not in item7.content
    assert "consolidated financial statements" not in item7.content


def test_extract_10k_item7_table_numbers_stripped_prose_intact() -> None:
    # Live-verified bug: a raw MD&A financial table tag-strips into bare numbers
    # with no sentence structure, contaminating the chunk (number_guard then
    # correctly blocks queries retrieving it). Table cells must never leak...
    item7 = extract_10k_prose(_build_10k_html())[1]
    for leaked in (
        _TABLE_PRODUCTS_NET,
        _TABLE_PRODUCTS_PRIOR,
        _TABLE_SERVICES_NET,
        _TABLE_SERVICES_PRIOR,
        _TABLE_PRODUCTS_PCT,
        _TABLE_SERVICES_PCT,
    ):
        assert leaked not in item7.content
    # ...but the narrative prose immediately before AND after the stripped
    # table survives, and the two don't fuse into one run-on line.
    assert _MDNA_SENTINEL in item7.content
    assert _MDNA_TAIL_SENTINEL in item7.content
    assert "following table summarizes net sales" in item7.content
    assert "further narrative discussion" in item7.content


def test_html_to_text_strips_nested_tables_and_keeps_surrounding_prose() -> None:
    # Nesting-aware: the inner </table> must not stop the skip early (which
    # would let "222,222" and the tail of the outer row leak through), and the
    # outer </table> must resume normal text collection afterward.
    html = (
        "<p>Before table.</p>"
        "<table><tr><td>Outer 111,111"
        "<table><tr><td>Inner 222,222</td></tr></table>"
        "trailing outer cell text</td></tr></table>"
        "<p>After table.</p>"
    )
    text = _html_to_text(html)
    assert "111,111" not in text
    assert "222,222" not in text
    assert "trailing outer cell text" not in text
    assert "Before table." in text
    assert "After table." in text


def test_extract_10k_toc_links_not_mistaken_for_headings() -> None:
    # The TOC "Item 1. Business" / "Item 7. MD&A" links are short; the extracted
    # sections are the full body regions, so both clear the length floor by far.
    item1, item7 = extract_10k_prose(_build_10k_html())
    assert len(item1.content) > _MIN_LEN_SANITY
    assert len(item7.content) > _MIN_LEN_SANITY


def test_extract_10k_forward_cross_reference_ignored() -> None:
    # The forward "see Item 7. Management's Discussion below" ref sits inside Item
    # 1 before the real Item 7 heading. A naive longest-span search would start
    # Item 7 there and swallow the Business tail + Risk Factors. The heuristic
    # must instead anchor on the real heading, so neither leaks into Item 7.
    item7 = extract_10k_prose(_build_10k_html())[1]
    assert _BIZ_SENTINEL not in item7.content
    assert _BIZ_TAIL_SENTINEL not in item7.content
    assert _RISK_SENTINEL not in item7.content


def test_extract_10k_backward_cross_reference_kept_in_body_not_boundary() -> None:
    # The backward "Item 1. Business" ref lives inside Item 7's body; it has no
    # Item 1A after it, so it is not treated as an Item 1 heading -- yet its
    # surrounding MD&A prose stays part of Item 7.
    item1, item7 = extract_10k_prose(_build_10k_html())
    assert "demand remained strong" in item7.content
    # Item 1 is still the Part I region, not this later reference.
    assert item1.content.startswith("Item 1.")
    assert _BIZ_SENTINEL in item1.content


def test_extract_10k_inline_xbrl_visible_text_kept_hidden_dropped() -> None:
    item1 = extract_10k_prose(_build_10k_html())[0]
    # <ix:nonNumeric> visible text is kept...
    assert "Apple Inc." in item1.content
    # ...but the <ix:hidden> metadata number never leaks into any section.
    for section in extract_10k_prose(_build_10k_html()):
        assert _HIDDEN_NOISE not in section.content


def test_extract_10k_missing_item7_raises_naming_item() -> None:
    with pytest.raises(SecDocumentParseError) as exc:
        extract_10k_prose(_build_10k_html(include_item7=False))
    assert "Item 7" in str(exc.value)


def test_extract_10k_short_item1_raises_naming_item() -> None:
    with pytest.raises(SecDocumentParseError) as exc:
        extract_10k_prose(_build_10k_html(short_business=True))
    msg = str(exc.value)
    assert "Item 1" in msg
    assert "short" in msg.lower()


def test_html_to_text_folds_nbsp_and_preserves_paragraph_breaks() -> None:
    # &#160; -> \xa0 -> " "; block tags become newlines so headings stay findable.
    text = _html_to_text("<p>Item&#160;1.</p><p>Business</p><div>Next</div>")
    assert "Item 1." in text
    assert "\n" in text  # paragraph boundary preserved
    assert "\xa0" not in text  # nbsp folded to a plain space
