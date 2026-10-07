"""Pipeline: detect → try strategies in order → first hit wins → write to disk."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

from .detect import fingerprint
from .models import Bundle, Fingerprint
from .strategies import STRATEGIES, STRATEGIES_BY_NAME

log = logging.getLogger(__name__)


def run(
    url: str, name: str | None = None, strategy: str | None = None
) -> Bundle | None:
    """Extract a doc site to a Bundle.

    Args:
        url:      Entry URL.
        name:     Output slug. Defaults to host.
        strategy: Force a specific strategy. None = try all in order.
    """
    name = name or _slug_from_url(url)
    fp = fingerprint(url)
    log.info("detected platform=%s confidence=%.2f", fp.platform, fp.confidence)

    if strategy:
        strat = STRATEGIES_BY_NAME.get(strategy)
        if not strat:
            log.error("unknown strategy: %s", strategy)
            return None
        return _try(strat, fp, name)

    # If detection found an explicit OpenAPI URL hint, try openapi first —
    # before llms_txt which may produce sparse 1-page results for the same site.
    ordered = list(STRATEGIES)
    if fp.openapi_url_hint:
        openapi_strat = STRATEGIES_BY_NAME.get("openapi")
        if openapi_strat:
            ordered = [openapi_strat] + [s for s in ordered if s is not openapi_strat]

    produced_empty_bundle = False
    for strat in ordered:
        if not strat.can_handle(fp):
            log.debug("skip %s (can_handle=False)", strat.name)
            continue
        bundle = _try(strat, fp, name)
        if bundle and bundle.pages:
            log.info("strategy '%s' won with %d pages", strat.name, len(bundle.pages))
            return bundle
        if bundle is not None and not bundle.pages:
            produced_empty_bundle = True

    _log_no_bundle(url, fp, produced_empty_bundle)
    return None


def _try(strat, fp: Fingerprint, name: str) -> Bundle | None:
    try:
        return strat.extract(fp, name)
    except Exception as e:
        log.exception("strategy %s raised: %s", strat.name, e)
        return None


def _log_no_bundle(url: str, fp: Fingerprint, produced_empty_bundle: bool) -> None:
    """Log the no-bundle outcome and attach structured Sentry context.

    Logs at ERROR when a strategy returned a non-None Bundle with empty pages
    (likely a regression). Logs at WARNING for the expected unsupported-site case
    (every strategy returned None) so it is only a Sentry breadcrumb, not an event.
    """
    msg = (
        "strategy returned empty bundle for url=%s platform=%s confidence=%.2f"
        if produced_empty_bundle
        else "no strategy produced a bundle for url=%s platform=%s confidence=%.2f"
    )
    log_fn = log.error if produced_empty_bundle else log.warning

    # Best-effort telemetry: enriching the Sentry scope must never interfere
    # with the log emit below or the caller's None return, so any failure
    # (SDK absent, not initialised, internal error) is swallowed by design.
    # Set before the log emit so LoggingIntegration captures the context.
    try:
        import sentry_sdk

        sentry_sdk.set_context(
            "techdoc_pipeline",
            {
                "url": url,
                "platform": fp.platform,
                "confidence": fp.confidence,
                "produced_empty_bundle": produced_empty_bundle,
            },
        )
    except Exception:
        pass

    log_fn(msg, url, fp.platform, fp.confidence)


def write_bundle(bundle: Bundle, output_dir: Path) -> None:
    """Write a bundle to disk: one .md per page + index.json manifest."""
    out = output_dir / bundle.name
    out.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": bundle.name,
        "entry_url": bundle.entry_url,
        "platform": bundle.platform.value,
        "strategy": bundle.strategy,
        "fetched_at": bundle.fetched_at.isoformat(),
        "page_count": bundle.page_count,
        "total_words": bundle.total_words,
        "pages": [],
    }
    seen_slugs: dict[str, int] = {}
    for i, page in enumerate(bundle.pages):
        slug = _slug_for_page(page, fallback=f"page-{i:03d}")
        # Deduplicate: if two pages produce the same slug, append a counter.
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0
        filename = f"{i:03d}-{slug}.md"
        path = out / filename
        path.write_text(
            f"---\n"
            f"title: {page.title}\n"
            f"source_url: {page.url}\n"
            f"strategy: {page.source_strategy}\n"
            f"---\n\n"
            f"{page.markdown}\n",
            encoding="utf-8",
        )
        manifest["pages"].append(
            {
                "file": filename,
                "url": page.url,
                "title": page.title,
                "word_count": page.word_count,
                "strategy": page.source_strategy,
                "metadata": page.metadata,
            }
        )

    (out / "index.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _slug_from_url(url: str) -> str:
    host = urlparse(url).netloc
    return _safe_slug(host, fallback="site")


def _slug_for_page(page, fallback: str) -> str:
    """Choose a descriptive slug for a page filename.

    - OpenAPI pages: use the title (tag-based, already unique and meaningful,
      e.g. "API Banking — Extrato" → "api-banking-extrato").
    - All other strategies: derive from the URL path (unique per page, more
      descriptive than a generic site title that repeats across all pages,
      e.g. "/docs/introducao/sobre-este-portal" → "docs-introducao-sobre-este-portal").
    - Falls back to title if URL has no meaningful path, then to fallback.
    """
    if page.source_strategy == "openapi":
        return _safe_slug(page.title, fallback=fallback)

    parsed = urlparse(page.url)
    path = parsed.path.strip("/")
    if path:
        # URL-decode then normalize accents before slugging so that
        # e.g. "%C3%A7%C3%B5es" → "oes" instead of "-c3-a7-c3-b5es".
        path = unquote(path).replace("/", "-")
        return _safe_slug(path, fallback=fallback)

    return _safe_slug(page.title, fallback=fallback)


def _safe_slug(text: str, fallback: str) -> str:
    # URL-decode, then strip accents via NFKD decomposition.
    text = unquote(text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback
