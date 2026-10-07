"""Docusaurus strategy.

Docusaurus does SSR — the rendered HTML contains the actual content, no
JS required for extraction. The site structure is enumerable via
/sitemap.xml.

Algorithm:
  1. Fetch /sitemap.xml.
  2. Filter to same-host URLs that look like docs paths (skip versioned
     duplicates, /blog/* unless requested).
  3. For each, fetch and extract `.theme-doc-markdown` (Docusaurus 2/3
     content wrapper) or `article`.
  4. Convert with markdownify.
  5. Bonus: if the main JS bundle embeds redocusaurus spec paths (common
     pattern: Docusaurus + Redoc plugin), extract those specs too and
     append as OpenAPI-sourced pages.

TODO (Claude Code):
  - Some Docusaurus sites publish source markdown on GitHub. If we can
    detect the repo (often in `<meta property="og:site_name">` or in a
    "Edit this page" link), prefer raw.githubusercontent over scraping.
  - De-duplicate /next/, /v1/, /v2/ versioned paths — keep the unversioned
    or "latest" only.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import httpx
from markdownify import markdownify
from selectolax.parser import HTMLParser

from fluxyr.logging import carry_context

from ..http import get_with_retry, make_client
from ..models import Bundle, Fingerprint, Page, Platform
from .base import Strategy
from .openapi import OpenApiStrategy

log = logging.getLogger(__name__)

_CONTENT_SELECTORS = [
    "article .theme-doc-markdown",
    "article .markdown",
    "main article",
    "article",
    "main",
]

_MAX_PAGES = 300
_SKIP_PATH_FRAGMENTS = ("/blog/", "/tags/", "/page/")


def _title_from_url(url: str) -> str:
    """Humanize the last path segment of a URL as a fallback title."""
    path = urlparse(url).path.strip("/")
    if not path:
        return url
    segments = [s for s in path.split("/") if s]
    segment = segments[-1] if segments else path
    segment = unquote(segment)
    segment = (
        unicodedata.normalize("NFKD", segment).encode("ascii", "ignore").decode("ascii")
    )
    return segment.replace("-", " ").replace("_", " ").title()


def _extract_title(node, tree, url: str) -> str:
    """Extract page title with multiple fallback strategies.

    1. H1 inside the content node (standard Docusaurus .theme-doc-markdown).
    2. <title> tag with site-suffix stripped ("Page | Site" → "Page").
    3. URL path last segment, humanized.
    """
    # 1. H1 inside content area (works for standard Docusaurus themes).
    h1 = node.css_first("h1")
    if h1:
        t = h1.text().strip()
        # Reject if it matches the full-page <title> (site-wide nav header).
        full_title = tree.css_first("title")
        full_text = full_title.text().strip() if full_title else ""
        if t and t not in full_text:
            return t

    # 2. <title> tag: "Page Title | Site Name" — take the first part.
    if full_title or (full_title := tree.css_first("title")):
        raw = full_title.text().strip()
        parts = [p.strip() for p in raw.split(" | ")]
        if len(parts) >= 2 and parts[0] and parts[0] != parts[-1]:
            return parts[0]

    # 3. URL path as last resort.
    return _title_from_url(url)


class DocusaurusStrategy(Strategy):
    name = "docusaurus"

    def can_handle(self, fp: Fingerprint) -> bool:
        return fp.platform == Platform.DOCUSAURUS

    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        client = make_client()
        sitemap = fp.sitemap_url
        if not sitemap:
            # Best-effort guess.
            parsed = urlparse(fp.url)
            sitemap = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

        urls = self._discover_urls(client, sitemap, base=fp.url)
        if not urls:
            return None

        with ThreadPoolExecutor(max_workers=15) as ex:
            results = list(
                ex.map(
                    carry_context(lambda u: self._extract_page(client, u)),
                    urls[:_MAX_PAGES],
                )
            )
        pages: list[Page] = [p for p in results if p is not None]

        # Also extract any redocusaurus OpenAPI specs embedded in the JS bundle.
        openapi_pages = self._extract_redocusaurus_specs(client, fp)
        pages.extend(openapi_pages)

        if not pages:
            return None
        return Bundle(
            name=name,
            entry_url=fp.url,
            platform=fp.platform,
            strategy=self.name,
            pages=pages,
        )

    @staticmethod
    def _extract_redocusaurus_specs(
        client: httpx.Client, fp: Fingerprint
    ) -> list[Page]:
        """Find redocusaurus YAML/JSON spec paths embedded in the Docusaurus JS bundle.

        Docusaurus sites that use the redocusaurus plugin embed the spec file
        paths as relative strings like 'redocusaurus/swagger-banking-yaml.yaml'
        in the compiled main bundle. We scan the bundle, resolve those paths
        relative to the site root, and extract each spec with OpenApiStrategy.
        """
        base_url = fp.signals.get("final_url") or fp.url
        parsed = urlparse(str(base_url))
        root = f"{parsed.scheme}://{parsed.netloc}"

        bundle_url = fp.signals.get("main_js_bundle")
        if not bundle_url:
            return []

        r = get_with_retry(client, bundle_url)
        if not r or r.status_code >= 400:
            return []

        spec_paths = list(
            set(
                re.findall(
                    r'redocusaurus/[^"\']+\.(?:json|ya?ml)', r.text, re.IGNORECASE
                )
            )
        )
        if not spec_paths:
            return []

        log.info(
            "docusaurus: found %d redocusaurus spec(s) in JS bundle", len(spec_paths)
        )
        openapi = OpenApiStrategy()
        pages: list[Page] = []
        for rel_path in sorted(spec_paths):
            spec_url = f"{root}/{rel_path}"
            spec = OpenApiStrategy._try_fetch_spec(client, spec_url)
            if not spec:
                log.warning("docusaurus: could not fetch spec %s", spec_url)
                continue
            try:
                spec_pages = openapi._spec_to_pages(spec, spec_url)
                pages.extend(spec_pages)
                log.info(
                    "docusaurus: extracted %d pages from %s", len(spec_pages), rel_path
                )
            except Exception as e:
                log.warning("docusaurus: spec parse failed for %s: %s", spec_url, e)

        return pages

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
        urls = [el.text for el in root.findall(".//sm:loc", ns) if el.text]
        host = urlparse(base).netloc
        filtered = [
            u
            for u in urls
            if urlparse(u).netloc == host
            and not any(frag in u for frag in _SKIP_PATH_FRAGMENTS)
        ]
        return filtered

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
        title = _extract_title(node, tree, url)
        for kill in node.css(
            "script, style, nav, footer, .theme-doc-toc-desktop, .pagination-nav"
        ):
            kill.decompose()
        md = markdownify(node.html or "", heading_style="ATX", strip=["img"]).strip()
        md = re.sub(r"\n{3,}", "\n\n", md)
        if len(md.split()) < 20:
            return None  # Likely a stub / redirect page.
        return Page(
            url=url,
            title=title,
            markdown=md,
            source_strategy="docusaurus",
            word_count=len(md.split()),
        )
