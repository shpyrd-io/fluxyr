"""Distillation: turn a raw Bundle into a compact integration reference via LLM.

Given a Bundle (potentially 200 pages, 500K words), select the most relevant pages
(auth, errors, overview, high API-signal endpoints) and ask the configured LLM to
produce a structured spec — the minimal document an AI needs to write integration code.

For bundles too large to fit in a single LLM call, the content is split into
chunks, each distilled separately, then merged with a second LLM call.

Output structure (always the same, regardless of source):
  ## Authentication
  ## Base URL
  ## Errors
  ## Special Notes  (rate limits, pagination, idempotency, gotchas)
  ## Endpoints      (method, path, params, request/response schema, example)

LLM calls use the installation adapter supplied through llm.adapter_factory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .distill_llm_calls import distill_chunk, merge_partials
from .distill_page_selection import select_pages
from .llm import get_llm
from .models import Bundle, Page
from .shrink import _shrink_level, shrink_pages

log = logging.getLogger(__name__)

# Selection budget multiplier for chunked-sized providers (Anthropic).
# Gemini's chunk_words=300K already saturates the cap, so its effective multiplier
# stays at 8× (300K*8 > 320K cap → capped at 320K words ≈ 320K words, unchanged).
# Anthropic's chunk_words=40K: 4 * 40K = 160K (halved, reducing chunked-path cost).
# Cap of 320_000 prevents runaway selection on any future high-capacity provider.
_SELECT_BUDGET_MULTIPLIER = 4
_SELECT_BUDGET_CAP = 320_000


# ── Context builders ───────────────────────────────────────────────────────────


def _chunk_pages(pages: list[Page], chunk_words: int) -> list[list[Page]]:
    """Split pages into chunks that each fit within chunk_words."""
    chunks: list[list[Page]] = []
    current: list[Page] = []
    words = 0
    for page in pages:
        if words + page.word_count > chunk_words and current:
            chunks.append(current)
            current, words = [page], page.word_count
        else:
            current.append(page)
            words += page.word_count
    if current:
        chunks.append(current)
    return chunks


# ── Public API ────────────────────────────────────────────────────────────────


def distill(
    bundle: Bundle,
    endpoints: list[str] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    *,
    usage_callback: Callable[[dict], None] | None = None,
    execution_id: str | None = None,
) -> str:
    """Distill a Bundle into a compact integration reference.

    Uses the installation model adapter for distillation.
    For large bundles, splits into chunks and merges the results.
    When the total content fits within the LLM's single-shot budget, sends it
    in a single call (no merge step).

    Args:
        bundle: The extracted documentation bundle.
        endpoints: Optional list of endpoint paths.  Currently unused — accepted for
            API compatibility and reserved for future prioritisation logic.
        on_progress: Optional callback called with a progress dict at each sub-step.
            Keys: type='techdoc_progress', phase, chunk, total_chunks, pages, words.
        usage_callback: Optional keyword-only callback invoked with a usage dict
            (provider, model, request_id, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens) after each successful LLM API
            call (once per chunk, plus once for the merge step when chunked).
            A failed attempt (all retries exhausted, or a non-transient error)
            reports nothing — there is no partial-usage recovery path. Any
            exception from this callback is swallowed — callers MUST NOT rely
            on it being called. Default None keeps CLI / standalone use working
            with zero app imports (this package is app-independent).
    """
    llm = get_llm()

    # Selection budget: 4× chunk_words for chunked-sized providers (Anthropic 4*40K=160K);
    # Gemini's chunk_words=300K would give 4*300K=1.2M but is capped at 320K — effectively
    # the same as the old 8*40K for any provider whose chunk_words is large.
    budget_words = min(_SELECT_BUDGET_MULTIPLIER * llm.chunk_words, _SELECT_BUDGET_CAP)
    pages = select_pages(bundle, budget_words=budget_words)
    if not pages:
        raise RuntimeError("No pages selected — bundle may be empty")

    # Apply deterministic shrink pass before token estimation and LLM calls.
    pages, shrink_stats = shrink_pages(pages, _shrink_level())
    log.info(
        "distill: shrink level=%d pages=%d words %d→%d (passes: %s)",
        shrink_stats["level"],
        shrink_stats["pages"],
        shrink_stats["words_before"],
        shrink_stats["words_after"],
        shrink_stats["passes"],
    )

    total_words = sum(p.word_count for p in pages)

    # Single-shot path: if the whole context fits within the LLM's budget,
    # send it in one call without chunking or merging.
    # Budget: max_input_tokens - 16K headroom for system prompt + model overhead,
    # capped at 700K to avoid extremely large prompts.
    single_shot_budget = min(llm.max_input_tokens - 16_000, 700_000)
    # Empirical ratio for table/code-heavy API markdown measured on the real
    # corpus (GitHub/Asana bundles): ~2.0-2.8 tokens per word. Using 2.0 keeps
    # Anthropic (200K ctx) reliably on the chunked path and Gemini's selection
    # cap (320K words -> <=~900K tokens) safely inside its 1M window. The old
    # prose-ish 1.33 under-estimated and let oversized payloads go single-shot.
    estimated_input_tokens = int(total_words * 2.0)

    if estimated_input_tokens <= single_shot_budget:
        log.info(
            "distill: single-shot path — %d pages, %d words (~%d tokens, budget %d)",
            len(pages),
            total_words,
            estimated_input_tokens,
            single_shot_budget,
        )
        if on_progress:
            on_progress(
                {
                    "type": "techdoc_progress",
                    "phase": "distilling_chunk",
                    "chunk": 1,
                    "total_chunks": 1,
                }
            )
        return distill_chunk(
            llm,
            bundle.name,
            pages,
            usage_callback=usage_callback,
            execution_id=execution_id,
        )

    # Chunked path: split pages and distill each chunk separately, then merge.
    chunks = _chunk_pages(pages, llm.chunk_words)

    log.info(
        "distill: splitting into %d chunks (llm.chunk_words=%d)",
        len(chunks),
        llm.chunk_words,
    )
    partials: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_words = sum(p.word_count for p in chunk)
        log.info(
            "distill: chunk %d/%d — %d pages, %d words",
            i + 1,
            len(chunks),
            len(chunk),
            chunk_words,
        )
        if on_progress:
            on_progress(
                {
                    "type": "techdoc_progress",
                    "phase": "distilling_chunk",
                    "chunk": i + 1,
                    "total_chunks": len(chunks),
                }
            )
        partials.append(
            distill_chunk(
                llm,
                bundle.name,
                chunk,
                usage_callback=usage_callback,
                execution_id=execution_id,
            )
        )

    log.info("distill: merging %d partials", len(partials))
    if on_progress:
        on_progress(
            {
                "type": "techdoc_progress",
                "phase": "distilling_merge",
                "total_chunks": len(partials),
            }
        )
    return merge_partials(
        llm,
        bundle.name,
        partials,
        usage_callback=usage_callback,
        execution_id=execution_id,
    )


def distill_to_file(
    bundle: Bundle, output_dir: Path, endpoints: list[str] | None = None
) -> Path:
    """Distill and write to <output_dir>/<bundle.name>/integration.md.

    Uses the adapter configured by the embedding engine.
    """
    result = distill(bundle, endpoints=endpoints)
    out = output_dir / bundle.name
    out.mkdir(parents=True, exist_ok=True)
    path = out / "integration.md"
    path.write_text(result, encoding="utf-8")
    log.info("distill: wrote %s", path)
    return path


def get_integration_docs(url: str, endpoints: list[str] | None = None) -> str:
    """High-level function for embedding as a tool in an AI agent.

    Takes any docs URL, extracts and distills it into a compact integration
    reference. Always returns a string — either the spec or a helpful error
    message explaining why it failed.

    Uses the adapter configured by the embedding engine.
    """
    from . import pipeline

    try:
        bundle = pipeline.run(url)
    except Exception as e:
        return (
            f"Extraction failed for {url}: {e}\n\n"
            "Consider pasting the relevant documentation manually."
        )

    if not bundle or not bundle.pages:
        return (
            f"Could not extract documentation from {url}.\n\n"
            "Possible reasons:\n"
            "- The site is a JavaScript SPA that requires a real browser to render.\n"
            "- The documentation requires authentication.\n"
            "- The URL points to a page with no extractable API content.\n\n"
            "Consider pasting the relevant documentation sections manually."
        )

    try:
        return distill(bundle, endpoints=endpoints)
    except Exception as e:
        return (
            f"Extracted {bundle.page_count} pages ({bundle.total_words} words) "
            f"from {url} but distillation failed: {e}\n\n"
            "The raw pages are available but could not be summarised."
        )
