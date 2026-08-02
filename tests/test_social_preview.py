from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "index.html"
PREVIEW = ROOT / "docs" / "assets" / "preview.png"
README = ROOT / "README.md"

PAGES_URL = "https://soulknight666.github.io/telegram-account-orchestrator/"
RAW_PREVIEW_URL = (
    "https://raw.githubusercontent.com/soulknight666/"
    "telegram-account-orchestrator/main/docs/assets/preview.png"
)
REPOSITORY_URL = "https://github.com/soulknight666/telegram-account-orchestrator"
RELEASES_URL = f"{REPOSITORY_URL}/releases"


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.hrefs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta":
            key = values.get("property") or values.get("name")
            content = values.get("content")
            if key and content:
                self.meta[key] = content
        elif tag == "link" and values.get("rel"):
            href = values.get("href")
            if href:
                self.links[values["rel"]] = href
        elif tag == "a" and values.get("href"):
            self.hrefs.add(values["href"])


def parse_site() -> MetadataParser:
    parser = MetadataParser()
    parser.feed(SITE.read_text(encoding="utf-8"))
    return parser


def test_social_page_uses_fixed_https_open_graph_metadata() -> None:
    parser = parse_site()

    assert parser.meta["og:url"] == PAGES_URL
    assert parser.meta["og:image"] == RAW_PREVIEW_URL
    assert parser.meta["twitter:image"] == RAW_PREVIEW_URL
    assert parser.meta["twitter:card"] == "summary_large_image"
    assert parser.meta["og:title"]
    assert parser.meta["og:description"]
    assert "x-amz-" not in parser.meta["og:image"].lower()
    assert "?" not in parser.meta["og:image"]


def test_social_page_has_canonical_repository_and_release_links() -> None:
    parser = parse_site()

    assert parser.links["canonical"] == PAGES_URL
    assert REPOSITORY_URL in parser.hrefs
    assert RELEASES_URL in parser.hrefs


def test_preview_asset_and_readme_share_entry_exist() -> None:
    assert PREVIEW.is_file()
    assert PREVIEW.stat().st_size > 0
    assert PAGES_URL in README.read_text(encoding="utf-8")
