"""ReadMe.com strategy.

ReadMe-hosted sites (Asaas, legacy iugu, many others) follow a consistent
pattern:
  - Sitemap at /sitemap.xml lists every page.
  - Pages are Next.js, so /_next/data/<build_id>/<slug>.json has the raw
    Markdown content of each page.
  - API references render from an OpenAPI spec — the spec is downloadable
    via the ReadMe public API: https://docs.readme.com/main/reference/getopenapi

POC implementation order:
  1. Crawl /sitemap.xml.
  2. For each page, GET the rendered HTML, extract the main content
     container (.rm-Article, .markdown-body, or similar — verify),
     convert with markdownify.

A nicer V2 would hit /_next/data/* but the build ID changes on every
deploy and would need to be scraped from the HTML first. Start with the
HTML path — it's robust.

TODO (Claude Code):
  - Verify the actual content selector by inspecting one ReadMe site
    (`techdoc detect https://docs.asaas.com/` then look at raw_html_snippet).
  - Add the _next/data shortcut as a fast path if the build ID is easy
    to extract from `__NEXT_DATA__`.
  - Hard-cap pages (e.g. 200) until we have rate-limiting.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from markdownify import markdownify
from selectolax.parser import HTMLParser

from ..http import get_with_retry, make_client
from ..models import Bundle, Fingerprint, Page, Platform
from .base import Strategy

log = logging.getLogger(__name__)

# Selectors to try for ReadMe content extraction, in order.
_CONTENT_SELECTORS = [
    "main .rm-Article",
    ".rm-Markdown",
    ".markdown-body",
    "main",
    "article",
]

_MAX_PAGES = 200


class ReadMeIoStrategy(Strategy):
    name = "readme_io"

    def can_handle(self, fp: Fingerprint) -> bool:
        return fp.platform == Platform.README_IO

    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        client = make_client()
        sitemap = fp.sitemap_url or self._guess_sitemap(fp.url)
        urls = self._discover_urls(client, sitemap, base=fp.url) if sitemap else []
        if not urls:
            log.warning("readme_io: no URLs from sitemap, aborting")
            return None

        pages: list[Page] = []
        for u in urls[:_MAX_PAGES]:
            page = self._extract_page(client, u)
            if page:
                pages.append(page)

        if not pages:
            return None
        return Bundle(
            name=name,
            entry_url=fp.url,
            platform=fp.platform,
            strategy=self.name,
            pages=pages,
        )

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _guess_sitemap(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    @staticmethod
    def _discover_urls(client, sitemap_url: str, base: str) -> list[str]:
        r = get_with_retry(client, sitemap_url)
        if not r or r.status_code >= 400:
            return []
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            return []
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls: list[str] = [el.text for el in root.findall(".//sm:loc", ns) if el.text]
        # Keep same-host URLs only.
        host = urlparse(base).netloc
        return [u for u in urls if urlparse(u).netloc == host]

    @staticmethod
    def _extract_page(client, url: str) -> Page | None:
        r = get_with_retry(client, url)
        if not r or r.status_code >= 400:
            return None
        tree = HTMLParser(r.text)
        node = None
        for sel in _CONTENT_SELECTORS:
            found = tree.css_first(sel)
            if found:
                node = found
                break
        if node is None:
            return None
        title_node = tree.css_first("h1") or tree.css_first("title")
        title = (title_node.text() if title_node else url).strip()
        # Strip script/style before conversion.
        for kill in node.css("script, style, nav, footer"):
            kill.decompose()
        md = markdownify(node.html or "", heading_style="ATX", strip=["img"]).strip()
        md = re.sub(r"\n{3,}", "\n\n", md)
        if not md:
            return None
        return Page(
            url=url,
            title=title,
            markdown=md,
            source_strategy="readme_io",
            word_count=len(md.split()),
        )
