"""Generic SSR site crawler.

For sites that serve real server-rendered HTML at each URL but have no
llms.txt, OpenAPI hint, or platform-specific extraction path.

Works for:
  - Custom static-site generators (e.g. iugu-new with AlpineJS)
  - Vue/Vuepress/VitePress static exports
  - Typesense docs (custom Vue.js)
  - Any site where different paths return distinct HTML content

Algorithm:
  1. Collect candidate URLs: sitemap.xml first; if absent, BFS link-follow.
  2. SPA guard: fetch two paths and compare HTML fingerprints. If identical
     content → true SPA (JS routing), return None so Playwright crawler runs.
  3. Fetch each URL with httpx (parallel), extract the richest content block,
     convert to Markdown with markdownify.
  4. Cap at _MAX_PAGES; skip pages with fewer than 30 words.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from markdownify import markdownify  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from fluxyr.logging import carry_context

from ..http import get_with_retry, make_client
from ..models import Bundle, Fingerprint, Page
from .base import Strategy

log = logging.getLogger(__name__)

_MAX_PAGES = 200
_MIN_WORDS = 30
# CSS selectors tried in order to find the main content block.
_CONTENT_SELECTORS = [
    "article",
    "main",
    '[role="main"]',
    ".docs-content",
    ".content",
    ".documentation",
    ".doc-content",
    "#content",
    "#main-content",
]


class GenericCrawlerStrategy(Strategy):
    name = "generic_crawler"

    def can_handle(self, fp: Fingerprint) -> bool:
        # Always try as a fallback (after all cheaper strategies).
        return True

    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        client = make_client()
        base_url = str(fp.signals.get("final_url") or fp.url)
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        # 1. Collect candidate URLs.
        # When a sitemap is available, use it — but only if it has URLs under the
        # same path prefix as the entry URL.  A root-level sitemap with only
        # marketing pages (e.g. typesense.org/sitemap.xml) is useless when the
        # docs live at /docs/*.  In that case fall back to BFS link-following.
        base_path = parsed.path.rstrip("/") or "/"
        sitemap_url = fp.signals.get("sitemap_xml") or fp.sitemap_url
        candidates: list[str] = []
        if sitemap_url:
            candidates = self._urls_from_sitemap(client, sitemap_url, origin)
            # Also try a docs-relative sitemap if the root one is thin.
            if len(candidates) < 10 and base_path and base_path != "/":
                alt_sitemap = urljoin(
                    base_url, base_path.rsplit("/", 1)[0] + "/sitemap.xml"
                )
                if alt_sitemap != sitemap_url:
                    alt = self._urls_from_sitemap(client, alt_sitemap, origin)
                    if len(alt) > len(candidates):
                        candidates = alt
            log.info("generic_crawler: %d URLs from sitemap", len(candidates))
        if len(candidates) < 10:
            candidates = self._urls_from_links(client, base_url, origin)
            log.info("generic_crawler: %d URLs from link-follow", len(candidates))

        if not candidates:
            return None

        # 2. SPA guard: compare two different paths.
        if self._is_spa(client, base_url, candidates):
            log.info(
                "generic_crawler: SPA detected — skipping (Playwright crawler will handle)"
            )
            return None

        # 3. Fetch and extract (parallel).
        pages = self._fetch_pages(client, candidates[:_MAX_PAGES], base_url)
        if not pages:
            return None

        log.info("generic_crawler: extracted %d pages", len(pages))
        return Bundle(
            name=name,
            entry_url=fp.url,
            platform=fp.platform,
            strategy=self.name,
            pages=pages,
        )

    # ── URL collection ───────────────────────────────────────────────────

    @staticmethod
    def _urls_from_sitemap(client, sitemap_url: str, origin: str) -> list[str]:
        """Parse sitemap.xml and return URLs belonging to this origin."""
        r = get_with_retry(client, sitemap_url)
        if not r or r.status_code >= 400:
            return []
        # Handle sitemap index (nested sitemaps).
        text = r.text
        if "<sitemapindex" in text:
            sub_urls = re.findall(r"<loc>([^<]+)</loc>", text)
            all_urls: list[str] = []
            for sub in sub_urls[:5]:  # only follow a few sub-sitemaps
                r2 = get_with_retry(client, sub.strip())
                if r2 and r2.status_code < 400:
                    all_urls.extend(re.findall(r"<loc>([^<]+)</loc>", r2.text))
            return [
                u.strip()
                for u in all_urls
                if urlparse(u.strip()).netloc == urlparse(origin).netloc
            ]

        urls = re.findall(r"<loc>([^<]+)</loc>", text)
        return [
            u.strip()
            for u in urls
            if urlparse(u.strip()).netloc == urlparse(origin).netloc
        ]

    @staticmethod
    def _urls_from_links(client, start_url: str, origin: str) -> list[str]:
        """BFS link-following up to _MAX_PAGES unique same-origin URLs."""
        # Normalize start URL to avoid trailing-slash duplicates.
        start_url = start_url.rstrip("/") or start_url
        visited: set[str] = set()
        queue: deque[str] = deque([start_url])
        found: list[str] = [start_url]
        parsed_origin = urlparse(origin)

        while queue and len(found) < _MAX_PAGES * 2:
            url = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            r = get_with_retry(client, url)
            if not r or r.status_code >= 400:
                continue
            if "html" not in r.headers.get("content-type", ""):
                continue

            tree = HTMLParser(r.text)
            for node in tree.css("a[href]"):
                href = node.attributes.get("href", "")
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                abs_url = urljoin(url, href).split("#")[0].rstrip("/")
                if not abs_url:
                    continue
                p = urlparse(abs_url)
                if p.netloc != parsed_origin.netloc:
                    continue
                # Skip assets and non-doc paths.
                if re.search(
                    r"\.(css|js|png|jpg|gif|svg|ico|woff|pdf|zip)$",
                    p.path,
                    re.IGNORECASE,
                ):
                    continue
                # Normalize index.html → strip to bare directory.
                if abs_url.endswith("/index.html"):
                    abs_url = abs_url[: -len("index.html")].rstrip("/")
                if not abs_url:
                    continue
                if abs_url not in visited and abs_url not in found:
                    found.append(abs_url)
                    queue.append(abs_url)

        return found

    # ── SPA detection ────────────────────────────────────────────────────

    @staticmethod
    def _is_spa(client, base_url: str, candidates: list[str]) -> bool:
        """Return True if most candidate URLs serve the same HTML shell."""

        # Fingerprint: content text from the richest content block.
        # Compare content blocks (not body) to avoid nav/sidebar noise.
        def _fp(url: str) -> str | None:
            r = get_with_retry(client, url)
            if not r or r.status_code >= 400:
                return None
            tree = HTMLParser(r.text)
            for tag in tree.css("script, style, noscript, nav, header, footer, aside"):
                tag.decompose()
            # Try to find the main content area first.
            for sel in _CONTENT_SELECTORS:
                node = tree.css_first(sel)
                if node:
                    text = node.text(strip=True)
                    if len(text) > 100:
                        return text[:1000]
            body = tree.css_first("body")
            return body.text(strip=True)[:1000] if body else r.text[:1000]

        base_fp = _fp(base_url)
        if not base_fp:
            return False

        # Normalised base for comparison: strip trailing slash.
        base_norm = base_url.rstrip("/")

        # Test up to 3 different paths.
        matches = 0
        tested = 0
        for url in candidates[1:10]:
            # Skip URLs that are just base URL variants (stripped slash, /index.html, etc.)
            url_norm = url.rstrip("/").removesuffix("/index.html").rstrip("/")
            if url_norm == base_norm or url_norm == base_norm.rstrip("/"):
                continue
            fp = _fp(url)
            if fp is None:
                continue
            tested += 1
            # Allow small variation (title changes etc.) but detect identical shells.
            similarity = sum(a == b for a, b in zip(base_fp[:500], fp[:500])) / 500
            if similarity > 0.92:
                matches += 1
            if tested >= 3:
                break

        if tested == 0:
            return False
        return matches / tested > 0.66  # >66% of tested paths look the same → SPA

    # ── Content extraction ───────────────────────────────────────────────

    def _fetch_pages(self, client, urls: list[str], base_url: str) -> list[Page]:
        def _fetch(url: str) -> Page | None:
            r = get_with_retry(client, url)
            if not r or r.status_code >= 400:
                return None
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype:
                return None
            return self._extract_page(r.text, url)

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(carry_context(_fetch), u): u for u in urls}
            pages: list[Page] = []
            for fut in as_completed(futures):
                p = fut.result()
                if p:
                    pages.append(p)

        # Sort by URL so output is deterministic.
        pages.sort(key=lambda p: p.url)
        return pages

    @staticmethod
    def _extract_page(html: str, url: str) -> Page | None:
        tree = HTMLParser(html)

        # Get title.
        title_node = tree.css_first("title")
        title = (title_node.text(strip=True) if title_node else "").strip()
        # Strip common suffixes like " | Company Docs".
        title = re.sub(r"\s*[|–—-]\s*.{3,40}$", "", title).strip() or urlparse(url).path

        # Find content block.
        content_node = None
        for sel in _CONTENT_SELECTORS:
            content_node = tree.css_first(sel)
            if content_node:
                break

        if not content_node:
            # Fallback: use body, remove nav/header/footer/aside.
            content_node = tree.css_first("body")
            if not content_node:
                return None
            for tag in content_node.css(
                "nav, header, footer, aside, [class*='sidebar'], [class*='nav'], [class*='menu']"
            ):
                tag.decompose()

        # Remove noise within content.
        for tag in content_node.css(
            "script, style, noscript, [class*='ad-'], [id*='cookie']"
        ):
            tag.decompose()

        md = markdownify(
            content_node.html or "",
            heading_style="ATX",
            strip=["script", "style", "noscript"],
            newline_style="backslash",
        )
        # Collapse excessive blank lines.
        md = re.sub(r"\n{3,}", "\n\n", md).strip()

        words = md.split()
        if len(words) < _MIN_WORDS:
            return None

        return Page(
            url=url,
            title=title,
            markdown=md,
            source_strategy="generic_crawler",
            word_count=len(words),
        )
