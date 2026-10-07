"""Platform fingerprinting.

Given a URL, fetch the HTML and a few well-known sibling URLs to guess
which documentation platform built the site. Return a Fingerprint with
a confidence score and any hints the strategies can use (e.g. an
OpenAPI URL we noticed in the HTML).

Detection is heuristic. Each signal contributes to a score. The highest
scoring platform wins, with a floor on confidence below which we return
UNKNOWN.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from fluxyr.logging import carry_context

from .http import get_with_retry, make_client
from .models import Fingerprint, Platform

log = logging.getLogger(__name__)


# Regex patterns are intentionally permissive — these sites mutate often.
_SIGNALS = {
    Platform.DOCUSAURUS: [
        (r"docusaurus", 0.4),
        (r"__DOCUSAURUS__", 0.6),
        (r'<meta name="generator" content="Docusaurus', 0.9),
    ],
    Platform.MINTLIFY: [
        (r"mintlify", 0.5),
        (r"__MINTLIFY_PROPS__", 0.9),
        (r"_mintlify", 0.4),
    ],
    Platform.README_IO: [
        (r"readme\.io", 0.6),
        (r"readme-app", 0.5),
        (r"rdmd-", 0.5),
        (r"\.readme\.io/", 0.7),
    ],
    Platform.STOPLIGHT: [
        (r"stoplight\.io", 0.7),
        (r"sl-elements", 0.6),
    ],
    Platform.REDOC: [
        (r"redoc-container", 0.8),
        (r"Redoc\.init\(", 0.9),
        (r'id="redoc"', 0.6),
    ],
    Platform.SWAGGER_UI: [
        (r"swagger-ui", 0.7),
        (r"SwaggerUIBundle", 0.9),
        (r'id="swagger-ui"', 0.6),
    ],
    Platform.GITBOOK: [
        (r"gitbook\.com", 0.7),
        (r"GitBook", 0.4),
    ],
}


def fingerprint(url: str) -> Fingerprint:
    """Identify the platform behind a documentation URL.

    Strategy:
      1. GET the URL.
      2. Score the HTML against all platform signature regexes.
      3. Look for sibling URLs that confirm (e.g. /openapi.json,
         /llms.txt, /sitemap.xml).
      4. Return the highest-scoring platform if it clears the threshold,
         otherwise UNKNOWN.
    """
    client = make_client()
    resp = get_with_retry(client, url)
    if resp is None or resp.status_code >= 400:
        return Fingerprint(
            url=url,
            platform=Platform.UNKNOWN,
            confidence=0.0,
            signals={"http_status": resp.status_code if resp else None},
        )

    html = resp.text
    signals: dict[str, object] = {
        "http_status": resp.status_code,
        "content_length": len(html),
        "final_url": str(resp.url),
    }

    # Short-circuit: if the URL itself is a raw OpenAPI/Swagger spec (e.g. GitHub raw YAML),
    # set openapi_url_hint to the URL and return immediately — no HTML signals needed.
    ctype = resp.headers.get("content-type", "")
    url_str = str(resp.url)
    if (
        "yaml" in ctype
        or "json" in ctype
        or url_str.endswith((".yaml", ".yml", ".json"))
    ) and not (
        html[:20].lower().startswith("<!doctype")
        or html[:20].lower().startswith("<html")
    ):
        first_line = html.lstrip()[:200]
        if re.search(
            r'(?:^|\n)\s*"?(openapi|swagger)"?\s*:', first_line, re.IGNORECASE
        ):
            log.info("detect: URL is a raw OpenAPI spec — skipping HTML fingerprinting")
            return Fingerprint(
                url=url,
                platform=Platform.UNKNOWN,
                confidence=0.0,
                signals=signals,
                openapi_url_hint=url_str,
            )

    scores: dict[Platform, float] = {}
    for platform, patterns in _SIGNALS.items():
        score = 0.0
        for pattern, weight in patterns:
            if re.search(pattern, html, flags=re.IGNORECASE):
                score = max(score, weight)
        if score > 0:
            scores[platform] = score

    # Look at the parsed DOM for additional hints.
    tree = HTMLParser(html)
    openapi_hint = _find_openapi_url(tree, base_url=str(resp.url))
    if openapi_hint:
        signals["openapi_url_in_html"] = openapi_hint
        # Strong vote for Redoc/Swagger UI if not already detected.
        scores.setdefault(Platform.REDOC, 0.5)

    # Store the main JS bundle URL so strategies can scan it without re-fetching HTML.
    main_bundle = _find_main_bundle(html, base_url=str(resp.url))
    if main_bundle:
        signals["main_js_bundle"] = main_bundle

    # Redocly-hosted sites inline the full spec in a state JS file.
    redocly_state = _find_redocly_state_url(html, base_url=str(resp.url))
    if redocly_state:
        signals["redocly_state_js"] = redocly_state

    # Probe known sibling endpoints in parallel.
    # Root-relative paths (leading /) probe at site root; relative paths (no /)
    # probe relative to the current URL path — useful when llms.txt lives under
    # a sub-directory like /docs/llms.txt rather than /llms.txt.
    _probe_paths: dict[str, str] = {
        "sitemap_xml": "/sitemap.xml",
        "llms_txt": "/llms.txt",
        "llms_full_txt": "/llms-full.txt",
        # Relative probes (only added when base path is non-root).
    }
    # Add relative llms probes when the URL has a meaningful sub-path.
    # Use a trailing-slash version of the URL so urljoin resolves relative to
    # the directory rather than replacing the last path segment.
    _resp_url_str = str(resp.url)
    _resp_url_dir = (
        _resp_url_str if _resp_url_str.endswith("/") else _resp_url_str + "/"
    )
    _base_path = _resp_url_str.split("?")[0].rstrip("/")
    _path_part = (
        _base_path.split("://", 1)[-1].split("/", 1)[-1]
        if "/" in _base_path.split("://", 1)[-1]
        else ""
    )
    if _path_part:  # has a non-root path component
        _probe_paths["llms_txt_rel"] = "llms.txt"
        _probe_paths["llms_full_txt_rel"] = "llms-full.txt"
        _probe_paths["sitemap_xml_rel"] = "sitemap.xml"

    with ThreadPoolExecutor(max_workers=min(len(_probe_paths), 8)) as ex:
        probe_futures = {
            key: ex.submit(
                carry_context(_probe),
                client,
                _resp_url_dir if key.endswith("_rel") else resp.url,
                path,
            )
            for key, path in _probe_paths.items()
        }
    for key, future in probe_futures.items():
        result = future.result()
        if result:
            base_key = key.removesuffix("_rel")
            existing = signals.get(base_key)
            if key.endswith("_rel"):
                # Relative probe: add as extra candidate alongside root.
                # Use a "_rel" suffix in signals so LlmsTxtStrategy can try both.
                if not existing:
                    # Root probe found nothing — use relative as primary.
                    signals[base_key] = result
                elif result != existing:
                    # Both root and relative found something different — keep both.
                    signals[base_key + "_rel"] = result
            else:
                signals[base_key] = result
    sitemap_url = signals.get("sitemap_xml")

    # Determine winner.
    if scores:
        platform, confidence = max(scores.items(), key=lambda kv: kv[1])
        if confidence < 0.4:
            platform = Platform.UNKNOWN
    else:
        platform, confidence = Platform.UNKNOWN, 0.0

    return Fingerprint(
        url=url,
        platform=platform,
        confidence=confidence,
        signals=signals,
        raw_html_snippet=html[:2000],
        openapi_url_hint=openapi_hint,
        sitemap_url=sitemap_url,
    )


# ── helpers ──────────────────────────────────────────────────────────


def _find_main_bundle(html: str, base_url: str) -> str | None:
    """Find the primary JS bundle URL from the raw HTML.

    Handles several common patterns:
      - Docusaurus:  /assets/js/main.<hash>.js
      - Generic SPA: main.js (relative), /main.js, /js/main.js
    Returns an absolute URL or None.
    """
    patterns = [
        r'src=["\'](/assets/js/main\.[^"\']+\.js)["\']',  # Docusaurus hashed bundle
        r'src=["\']([^"\']*\bmain\.[a-f0-9]+\.js)["\']',  # any hashed main.js
        r'src=["\']([^"\']*(?:/js/|/)main\.js)["\']',  # plain /main.js or /js/main.js
        r'src=["\'](\b[^"\']*\bmain\.js)["\']',  # relative main.js (e.g. starkbank)
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return urljoin(base_url, m.group(1))
    return None


def _find_openapi_url(tree: HTMLParser, base_url: str) -> str | None:
    """Look for an OpenAPI spec URL embedded in the page.

    Common patterns:
      - <redoc spec-url="..."/>
      - new SwaggerUIBundle({url: "..."})
      - data-spec-url="..."
    """
    for node in tree.css("[spec-url]"):
        v = node.attributes.get("spec-url")
        if v:
            return urljoin(base_url, v)
    for node in tree.css("[data-spec-url], [data-url]"):
        v = node.attributes.get("data-spec-url") or node.attributes.get("data-url")
        if v and (
            "openapi" in v.lower() or "swagger" in v.lower() or v.endswith(".json")
        ):
            return urljoin(base_url, v)

    # Plain <a href> links pointing directly to an OpenAPI/Swagger spec file.
    for node in tree.css("a[href]"):
        v = node.attributes.get("href", "")
        if (
            v
            and ("openapi" in v.lower() or "swagger" in v.lower())
            and re.search(r"\.(?:json|ya?ml)$", v, re.IGNORECASE)
        ):
            return urljoin(base_url, v)

    # Scan inline scripts for the common bootstrap patterns. Cheap regex.
    for script in tree.css("script"):
        text = script.text() or ""
        if not text:
            continue
        # SwaggerUIBundle / inline config: url: "openapi.json"
        m = re.search(
            r"""(?:spec-url|url)\s*[:=]\s*['"]([^'"]+\.(?:json|ya?ml))['"]""", text
        )
        if m:
            return urljoin(base_url, m.group(1))
        # Redoc.init() first argument
        m = re.search(r"""Redoc\.init\(\s*['"]([^'"]+)['"]""", text)
        if m:
            return urljoin(base_url, m.group(1))
        # .href = "/path/swagger.yaml" — dynamic download link (e.g. checkout.com)
        m = re.search(
            r"""\.href\s*=\s*['"]([^'"]+swagger[^'"]*\.(?:json|ya?ml))['"]""", text
        )
        if m:
            return urljoin(base_url, m.group(1))

    return None


def _find_redocly_state_url(html: str, base_url: str) -> str | None:
    """Find the Redocly inline state JS file URL (contains full OpenAPI spec)."""
    m = re.search(r"(redocly-state-[a-f0-9]+\.js)", html)
    if m:
        return urljoin(base_url, m.group(1))
    return None


def _probe(client, base, path: str) -> str | None:
    """HEAD/GET a sibling path. Return the URL if it exists, else None."""
    url = urljoin(str(base), path)
    try:
        r = client.head(url)
    except Exception:
        return None
    # Some servers reject HEAD; fall back to GET with stream=False small read.
    if r.status_code == 405 or r.status_code == 501:
        r = client.get(url)
    if 200 <= r.status_code < 300:
        return url
    return None
