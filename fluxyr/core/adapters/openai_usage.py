"""Token-usage un-nesting for the OpenAI-compatible wire.

Kept out of ``openai.py`` so the adapter module stays inside the repo file-size
budget. Everything here is pure: no SDK import, no I/O.
"""

from __future__ import annotations

from typing import Any

from fluxyr.core.adapters.usage_capture import (
    extract_openrouter_cost,
    read_count,
    read_field,
    should_replace_usage_report,
)

__all__ = [
    "build_usage_dict",
    "carve_out_nested_token_counts",
    "extract_openrouter_cost",
    "read_count",
    "read_field",
    "should_replace_usage_report",
]

# read_field / read_count / should_replace_usage_report / extract_openrouter_cost
# live in model_router.openai_usage_capture — the shared decisions used by
# BOTH OpenAI-wire usage producers (the gateway tee and this adapter's own
# streaming/non-streaming paths) — and are re-exported here so existing
# callers of this module (``OpenAIAdapter.get_token_usage``, ``openai_
# execution.execute_stream``, and this module's own tests) keep working
# unchanged.


def carve_out_nested_token_counts(usage_obj: Any) -> dict[str, int]:
    """Turn an OpenAI-wire ``usage`` object into MUTUALLY DISJOINT token counts.

    On this wire ``prompt_tokens_details.cached_tokens`` is a SUBSET of
    ``prompt_tokens``. billing_emitters bills every kind in the usage_dict
    independently, so emitting the parent verbatim next to the detail would
    bill every cached token TWICE. It is carved out here instead::

        input_tokens = prompt_tokens - cached_tokens

    ``max(0, ...)`` guards the subtraction so an upstream reporting an
    inconsistent detail count larger than its parent cannot produce a
    negative quantity on a ledger row.

    Reasoning tokens are DELIBERATELY left inside ``output_tokens`` on this
    path, unlike ``model_router.tee_openai._split_openai_usage`` (the gateway
    passthrough path), which carves ``completion_tokens_details
    .reasoning_tokens`` out into its own billed ``REASONING_TOKENS`` kind.
    That kind only prices from ``price_internal_reasoning_micros``
    (``pricing_synthesis``), which is commonly NULL or zero for OpenRouter
    reasoning models (the model catalog reports no ``internal_reasoning``
    price for most of them) — splitting reasoning out here would silently
    bill those tokens at zero, or drop them entirely when no price can be
    synthesised at all. Keeping them folded into ``output_tokens`` bills
    them at the completion rate, which is always priced.

    ``billing_service.resolve_pricing`` now has that fallback (its step 6:
    REASONING_TOKENS re-resolves as OUTPUT_TOKENS when no reasoning-specific
    price exists anywhere in the cascade), so reasoning tokens are billed at
    the completion rate on BOTH paths today. Keeping them folded here rather
    than split into their own kind is now a shape choice (one OUTPUT_TOKENS
    event vs. a separate REASONING_TOKENS event alongside it), not a
    billing-safety workaround — the two paths already charge the same money
    for the same consumption.

    ``audio_tokens`` is passed through unchanged (never subtracted), matching
    the gateway path. This is safe today only because no audio-capable model is
    reachable on the chat/brain path; if one is added, decide explicitly whether
    audio tokens are a subset of ``completion_tokens`` for that provider before
    billing both kinds.

    Args:
        usage_obj: the ``usage`` attribute of the last OpenAI-wire response
            or terminal streaming chunk. May be an SDK object or a dict.

    Returns:
        Dict with disjoint ``input_tokens``, ``output_tokens`` (reasoning
        included), ``cache_read_tokens``, ``reasoning_tokens`` (always 0 —
        kept for shape parity with the canonical usage_dict) and
        ``audio_tokens``. All ints, all >= 0.
    """
    prompt_tokens = read_count(usage_obj, "prompt_tokens")
    completion_tokens = read_count(usage_obj, "completion_tokens")
    prompt_details = read_field(usage_obj, "prompt_tokens_details")
    completion_details = read_field(usage_obj, "completion_tokens_details")

    cached = read_count(prompt_details, "cached_tokens")
    audio = read_count(completion_details, "audio_tokens")

    return {
        "input_tokens": max(0, prompt_tokens - cached),
        "output_tokens": completion_tokens,
        "cache_read_tokens": cached,
        "reasoning_tokens": 0,
        "audio_tokens": audio,
    }


def build_usage_dict(
    *,
    provider: str,
    model: str,
    last_response: Any,
    request_id: str | None = None,
) -> dict[str, Any] | None:
    """Build the usage_callback's usage_dict from an adapter's last response.

    Returns None when there is nothing to meter (no response captured this
    call, or a response whose ``usage`` attribute is absent/None) so the
    caller can skip invoking the callback entirely — kept here rather than
    inline in the adapter so OpenAIAdapter._fire_usage_callback stays a thin
    "build it, then invoke it" call site.

    ``request_id`` is the caller-supplied ACCUMULATED id (falls back to
    reading ``last_response.id`` when the caller passes/leaves it None): the
    non-streaming path has only one response, so ``read_field(last_response,
    'id')`` (i.e. ``response.id``) already IS the accumulated id; the
    streaming path passes the value ``openai_execution.execute_stream``
    tracked across every chunk — the last non-empty ``chunk.id`` seen up to
    and including the winning chunk, not just the winning chunk's OWN id —
    mirroring ``tee_openai.stream_tee_openai``'s accumulator so an earlier
    chunk's id still counts as the request's id when the winning chunk
    carries none. Falls back to None only when the wire itself carried NO id
    anywhere on the turn, so ``billing_emitters.record_token_usage`` builds
    NO ``provider_request_id`` (rather than a fake one) and its idempotency
    guard is simply not exercised for that turn — never bypassed with a
    wrong key.

    ``extract_openrouter_cost`` reads the SAME ``last_response`` for
    OpenRouter's authoritative ``upstream_cost_micros``/``serving_provider``
    (present as extra pydantic fields on the openai SDK's response/chunk
    objects — see that function's docstring) so first-party turns get the
    same cost capture the gateway tee already has.
    """
    if last_response is None:
        return None
    usage_obj = getattr(last_response, "usage", None)
    if usage_obj is None:
        return None

    split = carve_out_nested_token_counts(usage_obj)
    usage_dict: dict[str, Any] = {
        **extract_openrouter_cost(last_response),
        "provider": provider,
        "model": model,
        "input_tokens": split["input_tokens"],
        "output_tokens": split["output_tokens"],
        "cache_read_tokens": split["cache_read_tokens"],
        "cache_write_tokens": 0,
        "reasoning_tokens": split["reasoning_tokens"],
        "request_id": request_id or read_field(last_response, "id"),
    }
    if split["audio_tokens"] > 0:
        usage_dict["audio_tokens"] = split["audio_tokens"]
    return usage_dict
