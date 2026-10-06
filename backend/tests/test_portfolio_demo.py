"""Contracts for the static, read-only portfolio demo."""

from html.parser import HTMLParser
from pathlib import Path

DOCS_DIR = Path(__file__).parents[2] / "docs"
INDEX_PATH = DOCS_DIR / "index.html"


class DemoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_sources: list[str] = []
        self.animated_sources: list[str] = []
        self.element_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "img" and attributes.get("src"):
            self.image_sources.append(attributes["src"])
        if attributes.get("data-animated-src"):
            self.animated_sources.append(attributes["data-animated-src"])
        if attributes.get("id"):
            self.element_ids.add(attributes["id"])


def test_demo_is_a_disclosed_static_product_walkthrough() -> None:
    html = INDEX_PATH.read_text()
    parser = DemoHTMLParser()
    parser.feed(html)

    assert "read-only portfolio demo" in html.lower()
    assert "no live api calls" in html.lower()
    assert "captured october 3, 2026 with the current 18-company corpus" in html.lower()
    assert "https://filing-agent.vercel.app" in html
    assert "main-content" in parser.element_ids
    assert "screenshots/walkthrough_poster.png" in parser.image_sources
    assert "screenshots/answer_states_poster.png" in parser.image_sources
    assert "screenshots/walkthrough.gif" in parser.animated_sources
    assert "screenshots/answer_states.gif" in parser.animated_sources

    for source in parser.image_sources + parser.animated_sources:
        assert not source.startswith(("http://", "https://"))
        assert (DOCS_DIR / source).is_file()


def test_contact_details_live_on_a_separate_static_page() -> None:
    index = INDEX_PATH.read_text()
    contact = (DOCS_DIR / "contact.html").read_text()
    parser = DemoHTMLParser()
    parser.feed(index)

    assert 'id="contact" href="contact.html"' in index
    assert 'class="section contact"' not in index
    assert 'href="#contact"' not in index
    assert 'href="contact.html"' in index
    assert 'id="contact-title"' in contact
    assert 'href="index.html#walkthrough"' in contact
    assert 'src="contact.js" type="module"' in contact
    assert "no live API calls" in contact
    assert "mailto:mj.apps.support@gmail.com" in contact
    assert "https://github.com/mhju0/filing-digest/issues" in contact
    assert "browser's storage" in contact
    assert "GitHub General Privacy Statement" in contact
    assert "Gmail" in contact
