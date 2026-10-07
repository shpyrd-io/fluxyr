"""llms.txt / llms-full.txt strategy.

The simplest possible strategy: if the site publishes llms.txt
(https://llmstxt.org), it's already curated Markdown for LLMs. One fetch,
done.

Convention:
  - /llms.txt: short index, usually links + summaries.
  - /llms-full.txt: full content concatenated. Preferred when available.

Three modes (tried in order):
  1. Link-index format: llms.txt contains [Title](url.md) links. Fetch
     each .md file, return one page per document.
  2. Full-text sectioned: content has multiple # H1 headings (common in
     llms-full.txt). Split at H1 boundaries, return one page per section.
  3. Single-page: small/unsectioned content stored as-is.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from fluxyr_agent.logging import carry_context

from ..http import get_with_retry, make_client
from ..models import Bundle, Fingerprint, Page
from .base import Strategy

log = logging.getLogger(__name__)

_MAX_PAGES = 200
# Minimum H1 count to trigger section-splitting rather than single-page.
_MIN_H1_FOR_SPLIT = 3


class LlmsTxtStrategy(Strategy):
    name = "llms_txt"

    def can_handle(self, fp: Fingerprint) -> bool:
        return bool(fp.signals.get("llms_full_txt") or fp.signals.get("llms_txt"))

    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        client = make_client()

        # Try urls in preference order, skipping any that return HTML.
        for url in self._candidate_urls(fp):
            r = get_with_retry(client, url)  # type: ignore[arg-type]
            if not r or r.status_code >= 400:
                log.warning("llms.txt fetch failed: %s -> %s", url, r and r.status_code)
                continue
            text = r.text.strip()
            if not text:
                continue
            # Reject HTML responses (SPAs serving app shell for any path).
            # Check content-type header first, then fallback to body inspection.
            # lstrip() before prefix check handles responses that start with
            # whitespace or <!-- comments before the actual HTML tag.
            ctype = r.headers.get("content-type", "")
            if "html" in ctype:
                log.warning(
                    "llms.txt at %s returned HTML (content-type) — skipping", url
                )
                continue
            prefix = text.lstrip()[:20].lower()
            if (
                prefix.startswith("<!doctype")
                or prefix.startswith("<html")
                or prefix.startswith("<!--")
            ):
                log.warning("llms.txt at %s returned HTML (body) — skipping", url)
                continue
            # Good content found — process it.
            bundle = self._build_bundle(client, fp, name, url, text)
            if bundle and bundle.page_count >= 2:
                return bundle
            # Fewer than 2 pages — try next candidate (maybe it has better content).
            log.info(
                "llms.txt: %s gave %d pages, trying next candidate",
                url,
                bundle.page_count if bundle else 0,
            )

        return None

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _candidate_urls(fp: Fingerprint) -> list[str]:
        """Return URLs to try, preferred first.

        When the entry URL has a sub-path (e.g. /docs/), path-relative probes
        are tried before root ones: a relative llms.txt is specific to that
        section whereas the root one may cover unrelated products.
        When the entry URL is at the root, root probes come first.
        """
        from urllib.parse import urlparse as _urlparse

        _path = _urlparse(fp.url).path.strip("/")
        _has_subpath = bool(_path)  # non-root entry URL

        if _has_subpath:
            order = ("llms_full_txt_rel", "llms_full_txt", "llms_txt_rel", "llms_txt")
        else:
            order = ("llms_full_txt", "llms_full_txt_rel", "llms_txt", "llms_txt_rel")

        seen: set[str] = set()
        out: list[str] = []
        for key in order:
            u = fp.signals.get(key)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def _build_bundle(
        self, client, fp: Fingerprint, name: str, url: str, text: str, _depth: int = 0
    ) -> Bundle | None:
        # Mode 0: meta-index — llms.txt that links to other llms-full.txt files
        # (e.g. clerk.com/llms-full.txt which points to clerk.com/docs/llms-full.txt).
        # Only follow one level to avoid cycles.
        # Mode 0: only run on small meta-index files (< 5 KB, < 3 H1 sections).
        # Large files that already contain full content should not be treated as
        # meta-indexes even if they happen to link to other llms*.txt files.
        _is_meta = len(text) < 5_000 and len(re.findall(r"^# ", text, re.MULTILINE)) < 3
        if _depth == 0 and _is_meta:
            # Detect llms sub-links: markdown links AND plain URLs in the text.
            llms_sub_links_md = re.findall(
                r"\[([^\]]+)\]\((https?://[^\)]+llms(?:-full)?\.txt)\)", text
            )
            # Also find bare URLs (e.g. "see https://host/docs/llms-full.txt.")
            llms_plain_urls = re.findall(
                r"(?<!\()(https?://[^\s\)>]+llms(?:-full)?\.txt)(?=[.\s\n]|$)", text
            )
            llms_sub_links = llms_sub_links_md + [("", u) for u in llms_plain_urls]
            if llms_sub_links:
                # Prefer: (1) llms-full over llms, (2) path matches entry URL,
                # (3) "doc" in name/URL, (4) first. Never follow self-references.
                from urllib.parse import urlparse as _urlparse

                entry_path = _urlparse(fp.url).path.strip("/")
                _candidates = [u for _, u in llms_sub_links if u != url]
                preferred_url = (
                    next(
                        (
                            u
                            for u in _candidates
                            if "llms-full" in u
                            and (not entry_path or entry_path.split("/")[0] in u)
                        ),
                        None,
                    )
                    or next((u for u in _candidates if "llms-full" in u), None)
                    or next(
                        (
                            u
                            for u in _candidates
                            if entry_path and entry_path.split("/")[0] in u
                        ),
                        None,
                    )
                    or next((u for u in _candidates if "doc" in u.lower()), None)
                    or (_candidates[0] if _candidates else None)
                )
                if preferred_url:
                    r = get_with_retry(client, preferred_url)  # type: ignore[arg-type]
                    if r and r.status_code < 400:
                        sub_text = r.text.strip()
                        ctype = r.headers.get("content-type", "")
                        if (
                            sub_text
                            and "html" not in ctype
                            and not sub_text.lstrip()[:20]
                            .lower()
                            .startswith(("<!doctype", "<html", "<!--"))
                        ):
                            log.info(
                                "llms.txt: following meta-index link to %s",
                                preferred_url,
                            )
                            return self._build_bundle(
                                client, fp, name, preferred_url, sub_text, _depth=1
                            )

        # Mode 1: link-index (.md links). HTML-returning links are rejected by
        # _fetch_md_pages, so full-text files with incidental .md hrefs (e.g.
        # supabase llms-full.txt linking to codesandbox) produce 0 valid pages
        # and naturally fall through to the H1-split mode below.
        # Also support relative .md links (e.g. Twilio: [Title](/docs/page.md)).
        # Also support "markdown API" sites (e.g. PayPal) where the llms.txt
        # contains non-.md relative links with a hint about a /md/ path prefix.
        from urllib.parse import urljoin as _urljoin
        from urllib.parse import urlparse as _urlparse

        _base = url  # URL of the llms.txt file itself, for resolving relative links
        _base_origin = f"{_urlparse(_base).scheme}://{_urlparse(_base).netloc}"
        md_links_abs = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+\.md)\)", text)
        md_links_rel = re.findall(r"\[([^\]]+)\]\(((?:/|\.\.?/)[^\)]+\.md)\)", text)
        # Markdown-API mode: llms.txt hints that non-.md paths can be fetched via
        # a prefix transform (e.g. PayPal: /docs/x/ → /md/docs/x/).
        _md_api_links: list[tuple[str, str]] = []
        _md_api_prefix_match = re.search(
            r'"/md/"[^"\n]*"/docs/"'  # PayPal-style: "/md/" before "/docs/"
            r'|include\s+["\'/]md["\'/][^"\n]*["\'/]docs["\'/]'
            r"|/md/docs/",
            text,
        )
        if _md_api_prefix_match:
            # Extract non-.md relative links under /docs/ and apply the /md/ prefix.
            _plain_links = re.findall(r"\[([^\]]+)\]\((/docs/[^)#\s]+)\)", text)
            for t, rel in _plain_links:
                transformed = _base_origin + "/md" + rel
                _md_api_links.append((t, transformed))
            if _md_api_links:
                log.info(
                    "llms.txt: detected markdown-API pattern, transformed %d links",
                    len(_md_api_links),
                )
        md_links_raw = (
            md_links_abs
            + [(t, _urljoin(_base, u)) for t, u in md_links_rel]
            + _md_api_links
        )
        # Deduplicate by URL, preserving order (first title wins).
        _seen_urls: set[str] = set()
        md_links: list[tuple[str, str]] = []
        for t, u in md_links_raw:
            if u not in _seen_urls:
                _seen_urls.add(u)
                md_links.append((t, u))
        if md_links:
            # If the llms.txt root covers many services (e.g. docs.aws.amazon.com/llms.txt)
            # and the entry URL targets a specific service sub-path, filter to only
            # pages under that path. Only apply when:
            #  - entry path has depth ≥ 2 (e.g. /ses/ or /ses/latest/, not /api)
            #  - the filter would keep < 50% of total links (i.e. truly multi-service index)
            #  - at least 2 matching links remain after filtering
            from urllib.parse import urlparse as _urlparse

            entry_path = _urlparse(fp.url).path.rstrip("/")
            llms_path = _urlparse(url).path.rstrip("/")
            entry_depth = len([p for p in entry_path.split("/") if p])
            if (
                entry_depth >= 2
                and entry_path
                and llms_path
                and not llms_path.startswith(entry_path)
            ):
                filtered = [
                    (t, u) for t, u in md_links if entry_path in _urlparse(u).path
                ]
                if (
                    filtered
                    and len(filtered) < len(md_links) * 0.5
                    and len(filtered) >= 2
                ):
                    log.info(
                        "llms.txt: filtered %d→%d links to match entry path %s",
                        len(md_links),
                        len(filtered),
                        entry_path,
                    )
                    md_links = filtered
            log.info("llms.txt: found %d .md links, fetching each", len(md_links))
            pages = self._fetch_md_pages(client, md_links[:_MAX_PAGES])
            # Only trust Mode 1 result when it produced multiple pages and at
            # least half the links succeeded (for small indexes) or at least 5
            # pages (for large indexes). This prevents 1-2 incidental .md links
            # in an H1-split full-text file from blocking Mode 2.
            _mode1_ok = len(pages) >= 2 and (
                len(pages) >= 5 or len(pages) >= len(md_links) * 0.5
            )
            if pages and _mode1_ok:
                return Bundle(
                    name=name,
                    entry_url=fp.url,
                    platform=fp.platform,
                    strategy=self.name,
                    pages=pages,
                )
            if pages:
                log.info(
                    "llms.txt: Mode 1 gave %d/%d pages (too sparse), falling to H1-split",
                    len(pages),
                    len(md_links),
                )
            else:
                log.warning("llms.txt: .md link fetches all failed, falling back")

        # Mode 2: full-text with multiple H1 sections — split into pages.
        h1_positions = [m.start() for m in re.finditer(r"^# ", text, re.MULTILINE)]
        if len(h1_positions) >= _MIN_H1_FOR_SPLIT:
            pages = self._split_into_sections(text, url)
            if pages:
                log.info("llms.txt: split into %d sections from %s", len(pages), url)
                return Bundle(
                    name=name,
                    entry_url=fp.url,
                    platform=fp.platform,
                    strategy=self.name,
                    pages=pages,
                )

        # Mode 3: single page.
        page = Page(
            url=url,  # type: ignore[arg-type]
            title=f"{name} (llms.txt)",
            markdown=text,
            source_strategy=self.name,
            word_count=len(text.split()),
            metadata={"source_url": url},
        )
        return Bundle(
            name=name,
            entry_url=fp.url,
            platform=fp.platform,
            strategy=self.name,
            pages=[page],
        )

    @staticmethod
    def _split_into_sections(text: str, source_url: str) -> list[Page]:
        """Split a full-text llms-full.txt at top-level (H1) boundaries."""
        # Find all H1 heading positions.
        boundaries = [m.start() for m in re.finditer(r"^# ", text, re.MULTILINE)]
        boundaries.append(len(text))  # sentinel

        pages: list[Page] = []
        for i, start in enumerate(boundaries[:-1]):
            section = text[start : boundaries[i + 1]].strip()
            if not section:
                continue
            # Extract the H1 line as title.
            first_line = section.split("\n", 1)[0].lstrip("# ").strip()
            title = first_line or f"Section {i + 1}"
            if len(section.split()) < 10:
                continue
            pages.append(
                Page(
                    url=source_url,
                    title=title,
                    markdown=section,
                    source_strategy="llms_txt",
                    word_count=len(section.split()),
                )
            )
            if len(pages) >= _MAX_PAGES:
                break
        return pages

    @staticmethod
    def _fetch_md_pages(client, links: list[tuple[str, str]]) -> list[Page]:
        def _fetch_one(title_url: tuple[str, str]) -> Page | None:
            title, page_url = title_url
            r = get_with_retry(client, page_url)
            if not r or r.status_code >= 400:
                log.debug("llms.txt: skip %s (%s)", page_url, r and r.status_code)
                return None
            md = r.text.strip()
            prefix = md[:20].lower()
            if prefix.startswith("<!doctype") or prefix.startswith("<html"):
                return None
            if len(md.split()) < 10:
                return None
            return Page(
                url=page_url,
                title=title,
                markdown=md,
                source_strategy="llms_txt",
                word_count=len(md.split()),
            )

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(carry_context(_fetch_one), links))
        return [p for p in results if p is not None]
