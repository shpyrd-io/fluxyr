"""LLM-call helpers for distill(): emit usage, distill one chunk, merge partials.

Split out of distill.py to keep that module inside the project's file-size
budget (see .claude/guidelines/shared/code-quality.md, "Limit Source Files to
200-300 Lines") — these three functions are the only ones that touch
``llm.complete()`` and the usage callback, so they form a clean seam apart
from bundle/page selection and the top-level ``distill()`` orchestration.
"""

from __future__ import annotations

import logging

from .llm import LLMResult
from .models import Page

log = logging.getLogger(__name__)

# Default words-per-page truncation, kept in lockstep with distill._MAX_CHUNK_WORDS
# (Anthropic's chunk_words default) via the caller always passing an explicit
# max_words_per_page — this module never reads distill's module-level constant.
_MAX_CHUNK_WORDS = 40_000


# ── System prompts ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a technical documentation specialist. Extract a compact API integration \
reference from the documentation pages provided by the user.

Output ONLY these sections (omit a section entirely if information is absent):

# {service} — Integration Reference

## Authentication
How to obtain credentials. Exact header names and value format. Token lifetime / refresh.
If mTLS or client certificates are required: certificate format (PEM/PKCS12), how to obtain or
generate the client cert, required CN/SAN fields, CA chain to trust, and exact TLS config
(curl flags: --cert, --key, --cacert; or equivalent SDK/language config).

## Base URL

## Errors
HTTP status codes used. Error response JSON structure. When to retry.

## Special Notes
Rate limits, pagination pattern, idempotency keys, required custom headers, \
regional endpoints, or any behaviour that would silently break an integration.

## Endpoints
One sub-section per endpoint:
### <Verb> <Path>
**Parameters** — table: name | type | required | description (skip if none)
**Request body** — JSON schema or key fields
**Response** — key fields of the success response
**Example**
```
(minimal curl or JSON)
```

Rules:
- Concise. No marketing, no tutorials, no changelogs, no conceptual explanations.
- Preserve exact field names, types, and enum values — they are used for codegen.
- If a field name is self-explanatory, omit the description.
"""

_MERGE_SYSTEM = """\
You are merging {n} partial API integration references for '{service}' into one \
unified document.

Output a single document using this structure:
# {service} — Integration Reference
## Authentication  (include mTLS/certificate details if present)
## Base URL
## Errors
## Special Notes
## Endpoints (one ### sub-section per unique endpoint)

Rules:
- Deduplicate endpoints — keep the most complete version of each.
- Authentication and Base URL: use the most complete/specific version found.
- Errors: merge all unique status codes and descriptions.
- Special Notes: merge and deduplicate.
- Do not invent information not present in the partials.
- Be concise — this document is used for code generation.
"""


def _build_context(
    pages: list[Page], max_words_per_page: int = _MAX_CHUNK_WORDS
) -> str:
    """Build context string, truncating any individual page that exceeds the budget."""
    parts = []
    for p in pages:
        md = p.markdown
        words = md.split()
        if len(words) > max_words_per_page:
            md = (
                " ".join(words[:max_words_per_page])
                + "\n\n[content truncated — page too large]"
            )
        parts.append(f"### SOURCE: {p.title}\nURL: {p.url}\n\n{md}")
    return "\n\n---\n\n".join(parts)


def emit_usage(result: LLMResult, usage_callback):
    """Report token usage to the embedding engine, without billing."""
    if usage_callback:
        usage_callback(
            {
                "provider": result.provider,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
        )


def distill_chunk(
    llm,
    service: str,
    pages: list[Page],
    usage_callback=None,
    execution_id: str | None = None,
) -> str:
    result = llm.complete(
        system=_SYSTEM.format(service=service),
        # Per-page truncation follows the provider's chunk budget: on Gemini's
        # single-shot path a >40K-word page fits fine and must not be cut.
        user=_build_context(pages, max_words_per_page=llm.chunk_words),
        max_tokens=llm.max_output_tokens,
        execution_id=execution_id,
    )
    emit_usage(result, usage_callback)
    return result.text


def merge_partials(
    llm,
    service: str,
    partials: list[str],
    usage_callback=None,
    execution_id: str | None = None,
) -> str:
    combined = "\n\n---\n\n".join(
        f"## PARTIAL {i + 1}\n\n{p}" for i, p in enumerate(partials)
    )
    result = llm.complete(
        system=_MERGE_SYSTEM.format(n=len(partials), service=service),
        user=combined,
        max_tokens=llm.max_output_tokens,
        execution_id=execution_id,
    )
    emit_usage(result, usage_callback)
    return result.text
