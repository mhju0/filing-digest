"""SEC 10-K HTML prose extractor (primary-document HTML -> neutral ProseSection).

Companion to ``app.clients.sec`` (which stays fetch-only): this module turns a
fetched 10-K primary document's raw bytes into the *same* neutral
:class:`app.clients.dart.ProseSection` list that DART's ``extract_dsd_prose``
produces, so the downstream chunking/persist layer is source-agnostic.

Scope is deliberately narrow -- ONLY two items are extracted:
- **Item 1 (Business)**       -- what the company does.
- **Item 7 (MD&A)**           -- Management's Discussion and Analysis.
Every other item (1A Risk Factors, 7A Market Risk, 8 Financials, ...) is out of
scope; the sub-item headings ``Item 1A`` / ``Item 7A`` are used only as the
*end boundaries* of Items 1 and 7 respectively.

Approach (docs task scope):
1. Decode the bytes (modern 10-K primary docs are UTF-8 inline-XBRL HTML).
2. Strip tags to plain text with :class:`_TextExtractor` (stdlib ``html.parser``
   -- no new dependency), preserving paragraph boundaries as newlines and
   dropping non-visible inline-XBRL metadata (``<ix:header>``/``<ix:hidden>``)
   and ``<script>``/``<style>``.
3. Locate each item's body region by heading pattern (:func:`_locate_item`).

★ Fail-loud contract (mirrors the project's core principle): if either item
cannot be located, or its extracted text is suspiciously short (< 500 chars),
:class:`SecDocumentParseError` is raised naming the failing item. A silent empty
parse would ingest an empty corpus, so we never return silently-empty sections.

Heuristic for telling a real heading from noise -- the phrase "Item 1" also
appears in the table of contents and in cross-references ("see Item 7 below").
:func:`_locate_item` combines three signals so none of those are mistaken for the
body heading (see that function's docstring for the full rationale):
  (a) the ``Item N.`` occurrence must be immediately followed by the item's title
      word ("Business" / "Management") -- a bare "Item 7" reference is skipped;
  (b) it must have an ``Item NA`` sub-item boundary *after* it, with no other
      title-bearing ``Item N.`` heading in between -- this rejects a forward
      cross-reference that sits before the real heading;
  (c) among the survivors (typically the TOC entry and the body heading), the one
      spanning the most text wins -- the TOC entry spans only its own title, the
      body heading spans the whole section.

Numbers: like DART's ``extract_dsd_prose``, this extractor drops ``<table>``
content entirely (nesting-aware). A raw financial table (e.g. Item 7's segment
revenue breakdown) tag-strips into bare numbers with no sentence structure --
ingesting that as "prose" contaminates chunks with ungrounded digits that
number_guard then (correctly) blocks at answer time. Numbers stay sourced
exclusively from the structured financials API; MD&A prose keeps only the
narrative text around any tables.

Design mirrors ``app.clients.dart`` / ``app.clients.sec``: pure functions
(network-free, unit-tested directly) split from the (here, nonexistent) I/O.
"""

import logging
import re
from html.parser import HTMLParser

from app.clients.dart import ProseSection

logger = logging.getLogger(__name__)


class SecDocumentParseError(RuntimeError):
    """Raised when a 10-K item cannot be located or is suspiciously short.

    The message always names which item failed (Item 1 / Item 7) so a failed
    ingest points straight at the offending section rather than failing blind.
    """


# Block-level tags whose boundaries mark a paragraph break: a newline is emitted
# on both their start and end so tag-stripped text keeps its paragraph structure
# (otherwise "Item 7.Management's Discussion" would fuse and headings would be
# unfindable). Inline tags (<span>, <a>, <b>, <ix:nonNumeric>, ...) are NOT here
# -- their text flows inline, which is what we want. ``table`` and its descendants
# (thead/tbody/tr/td/th) are NOT here either -- ``table`` is handled separately by
# :class:`_TextExtractor`'s dedicated skip-depth counter, which drops all table
# content (see below); the descendant tags are kept in this set only as a
# defensive fallback for a stray tag that shows up outside a ``<table>`` in
# malformed markup.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "hr", "li", "ul", "ol",
        "thead", "tbody", "tr", "td", "th",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "blockquote",
    }
)

# Tags whose *content* is non-visible and must be dropped entirely. ``ix:header``
# wraps inline-XBRL metadata (``ix:hidden``, ``ix:references``, ``ix:resources``)
# that carries fact values never shown to a reader -- pulling it into prose would
# inject stray numbers/identifiers. ``script``/``style`` are the usual noise.
# ``table`` is handled by its own depth counter (:attr:`_TextExtractor._table_depth`)
# rather than living here, because unlike these three it must still emit a single
# paragraph-boundary newline around the dropped region (see ``handle_starttag``/
# ``handle_endtag``) so prose immediately before/after a table doesn't fuse.
_SKIP_CONTENT_TAGS = frozenset({"script", "style", "ix:header"})

# How far past an ``Item N.`` match to look for the title word. Generous enough to
# span a heading/title split across two blocks ("Item 1.\nBusiness") without
# reaching into the next paragraph.
_HINT_WINDOW = 120

# Fail-loud threshold: a real Item 1/7 body runs to many KB; anything under this
# is a mis-parse (e.g. only a TOC stub matched) and must raise, not ingest empty.
_MIN_ITEM_CHARS = 500

# Warn (do not fail) above this: the real Apple 10-K primary doc is ~1.5MB; a
# far larger body is worth a log line but still parsed whole.
_LARGE_DOC_WARN_BYTES = 8_000_000

# Canonical, stable section titles used as the ProseSection chunk header. The
# body's own heading text still leads ``content``; these give a clean, uniform
# citation label independent of per-filer heading wording.
_ITEM1_TITLE = "Item 1. Business"
_ITEM7_TITLE = "Item 7. Management's Discussion and Analysis"

# Unicode spaces that HTML entities (&nbsp; -> \xa0) and typography introduce;
# folded to a plain space before whitespace collapsing so ``Item\xa01.`` matches.
_UNICODE_SPACES = ("\xa0", " ", " ", " ", " ", " ", " ")


class _TextExtractor(HTMLParser):
    """Strip HTML to visible text, preserving paragraph boundaries as newlines.

    ``convert_charrefs=True`` (the default) means entities in text (``&amp;``,
    ``&nbsp;``, ``&#160;``) are already decoded when they reach
    :meth:`handle_data`. Content inside :data:`_SKIP_CONTENT_TAGS` is dropped via
    a small depth counter so nested same-name tags close correctly.

    ``<table>`` content is dropped the same way, via its own ``_table_depth``
    counter rather than reusing ``_skip_depth`` -- a nested ``<table>`` must
    still increment/decrement independently of any ``script``/``style``/
    ``ix:header`` skip in effect, and unlike those three, entering/leaving a
    table also emits one paragraph-boundary newline (only at depth 0->1 /
    1->0) so prose immediately before and after a stripped table doesn't fuse
    into a single run-on line.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0
        self._table_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _SKIP_CONTENT_TAGS:
            self._skip_tag = tag
            self._skip_depth = 1
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._parts.append("\n")
            return
        if self._table_depth:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag == "table":
            if self._table_depth:
                self._table_depth -= 1
                if self._table_depth == 0:
                    self._parts.append("\n")
            return
        if self._table_depth:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._table_depth:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _decode_html_bytes(raw: bytes) -> str:
    """Decode 10-K primary-document bytes to ``str`` (UTF-8 first).

    Modern inline-XBRL 10-Ks are UTF-8; ``cp1252`` covers legacy EDGAR HTML.
    Ordered strict attempts (mirroring dart's ``decode_dart_bytes`` philosophy),
    then a lossy UTF-8 fallback so one odd byte never aborts the whole ingest.
    """
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    logger.warning(
        "sec_document: %d bytes decoded as neither utf-8 nor cp1252; lossy utf-8",
        len(raw),
    )
    return raw.decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    """Strip tags to normalized visible text (paragraph boundaries as newlines).

    Folds Unicode spaces to a plain space, collapses intra-line whitespace, drops
    blank lines, and joins blocks with single newlines. The result feeds the
    heading regexes (whose ``\\s`` spans the newlines) and becomes the extracted
    prose. Pure -> unit-tested.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = parser.get_text()
    for space in _UNICODE_SPACES:
        text = text.replace(space, " ")
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _locate_item(
    text: str, num: str, sub: str, hint: str
) -> tuple[int, int, int] | None:
    """Locate one item's body region -> ``(start, end, span)`` or ``None``.

    ``num`` is the item number (``"1"``/``"7"``), ``sub`` its first sub-item that
    bounds the section (``"1A"``/``"7A"``), ``hint`` the lowercase title word that
    a genuine heading is followed by (``"business"``/``"management"``).

    Why this is not a naive "first Item N." search -- "Item 1"/"Item 7" also occur
    in the table of contents and in body cross-references. Three combined signals
    isolate the real body heading (see the module docstring):

    - **Title adjacency (a).** Only ``Item N.`` occurrences with ``hint`` within
      :data:`_HINT_WINDOW` chars count as heading-like ``hint_starts``. A bare
      "see Item 7 for details" (no "Management" adjacent) is dropped, and this is
      also what makes the fail-loud "cannot locate" meaningful.

    - **Bounded by its own sub-item (b).** A candidate keeps only if an ``Item NA``
      boundary exists after it AND no *other* hint-start ``Item N.`` heading sits
      between the two. This rejects a forward cross-reference ("see Item 7.
      Management's Discussion below") that appears inside Item 1 before the real
      Item 7 heading: the real heading falls between it and ``Item 7A``, so the
      cross-reference is discarded. A backward cross-reference ("as noted in Item
      1. Business" inside Item 7) has no ``Item 1A`` after it, so it is discarded
      too.

    - **Longest span wins (c).** Both the TOC entry and the body heading survive
      (a)+(b); the TOC entry spans only "Item N. <title>" up to the next TOC line,
      the body heading spans the whole section, so ``max(span)`` selects the body.

    Pure -> unit-tested.
    """
    ends = [m.start() for m in re.finditer(rf"item\s+{sub}\b", text, re.IGNORECASE)]
    if not ends:
        return None  # no sub-item boundary -> item not locatable

    hint_lower = hint.lower()
    hint_starts = [
        m.start()
        for m in re.finditer(rf"item\s+{num}\.", text, re.IGNORECASE)
        if hint_lower in text[m.start() : m.start() + _HINT_WINDOW].lower()
    ]

    best: tuple[int, int, int] | None = None
    for start in hint_starts:
        end = next((e for e in ends if e > start), None)
        if end is None:
            continue  # a heading with no sub-item boundary after it (cross-ref)
        if any(start < other < end for other in hint_starts):
            continue  # a real heading sits between -> this is the cross-ref, skip
        span = end - start
        if best is None or span > best[2]:
            best = (start, end, span)
    return best


def _extract_item(text: str, label: str, num: str, sub: str, hint: str) -> str:
    """Return one item's prose, or raise :class:`SecDocumentParseError` (fail-loud).

    ``label`` is the human item name used in error messages ("Item 1 (Business)").
    Raises when the region cannot be located, or when the extracted text is under
    :data:`_MIN_ITEM_CHARS` -- never returns a near-empty section.
    """
    located = _locate_item(text, num, sub, hint)
    if located is None:
        raise SecDocumentParseError(
            f"{label}: could not locate a heading region "
            f"(no 'Item {num}.' heading followed by its title with an "
            f"'Item {sub}' boundary after it)"
        )
    start, end, _span = located
    content = text[start:end].strip()
    if len(content) < _MIN_ITEM_CHARS:
        raise SecDocumentParseError(
            f"{label}: extracted text is suspiciously short "
            f"({len(content)} < {_MIN_ITEM_CHARS} chars); refusing to ingest a "
            f"near-empty section"
        )
    return content


def extract_10k_prose(raw_bytes: bytes) -> list[ProseSection]:
    """Extract Item 1 (Business) and Item 7 (MD&A) prose from a 10-K primary doc.

    Returns exactly two :class:`ProseSection` records in document order
    (``order`` 0 = Item 1, 1 = Item 7), each carrying a canonical
    ``section_title`` and the item's tag-stripped prose as ``content``.

    ★ Fail-loud: raises :class:`SecDocumentParseError` (naming the item) if either
    item is missing or its text is under :data:`_MIN_ITEM_CHARS`. Other items are
    out of scope; ``Item 1A`` / ``Item 7A`` serve only as end boundaries.
    """
    if len(raw_bytes) > _LARGE_DOC_WARN_BYTES:
        logger.warning(
            "extract_10k_prose: unusually large document (%d bytes); parsing whole",
            len(raw_bytes),
        )
    text = _html_to_text(_decode_html_bytes(raw_bytes))

    item1 = _extract_item(text, "Item 1 (Business)", num="1", sub="1A", hint="business")
    item7 = _extract_item(text, "Item 7 (MD&A)", num="7", sub="7A", hint="management")

    logger.info(
        "extract_10k_prose: Item 1 = %d chars, Item 7 = %d chars", len(item1), len(item7)
    )
    return [
        ProseSection(section_title=_ITEM1_TITLE, content=item1, order=0),
        ProseSection(section_title=_ITEM7_TITLE, content=item7, order=1),
    ]
