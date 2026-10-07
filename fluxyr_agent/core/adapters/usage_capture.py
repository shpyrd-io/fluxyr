"""openai_usage_capture — the usage-report replacement rule shared by both
OpenAI-wire usage producers: the gateway tee's ``tee_openai._process_data_line``
and the adapter's own streaming path, ``synthetic_brain.adapters.openai_execution
.execute_stream``.

Also home to ``extract_openrouter_cost``, shared by THREE OpenAI-wire usage
producers: the gateway tee (dict-shaped JSON, both stream and non-stream),
and the ``synthetic_brain`` adapter path (attribute-bearing openai-SDK
objects, both stream and non-stream) — see that function's own docstring.

Kept on the model_router side of the layering boundary (not in
``synthetic_brain.adapters.openai_usage``) so that ``model_router`` — the
single token-egress chokepoint — never has to import the much heavier
``synthetic_brain`` package (brain, memory, both provider SDKs) just to reach
this one decision. ``synthetic_brain.adapters.openai_usage`` imports FROM
this module, not the other way around: this module has no dependency on
``synthetic_brain`` at all, so importing it never triggers that package's
``__init__``.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_USD_TO_MICROS = Decimal(1000000)


def as_dict(value: Any) -> dict:
    """``value`` if it is a dict, else ``{}``.

    A malformed non-dict usage object or nested details object (a bare
    number, a string, a list) reads as "nothing here" instead of raising
    when a caller then does ``.get(...)`` on it.
    """
    return value if isinstance(value, dict) else {}


def read_field(obj: Any, key: str) -> Any:
    """Read one field off ``obj``, whether it is a dict or an attribute-bearing object.

    Accepts either shape uniformly: the openai SDK returns usage objects as
    attribute-bearing objects, while the gateway tee works from plain dicts
    parsed out of JSON. Applying the SAME dual accessor to both avoids the
    asymmetry where a dict-shaped candidate silently reads as "no field"
    while an attribute-bearing one reads fine.
    """
    if not obj:
        return None
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def read_count(obj: Any, key: str) -> int:
    """Read one integer count off ``obj`` via :func:`read_field`, defaulting to 0.

    Coerces a numeric-string value, and tolerates a non-numeric or
    out-of-range one (e.g. a JSON ``Infinity`` counter, which parses to
    ``float('inf')`` and raises ``OverflowError`` — an ``ArithmeticError``
    subclass — from ``int()``) by degrading to 0 rather than raising: the
    only callers of the function built on top of this run from a ``finally``
    block, where an exception would replace whatever error was already
    propagating, or silently drop billing for the request.
    """
    value = read_field(obj, key)
    if not value:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, ArithmeticError):
        return 0


def _is_real_usage_report(usage_obj: Any) -> bool:
    """True when a usage report carries a non-zero prompt+completion total.

    Both OpenAI-wire usage producers need the SAME definition of "real": the
    gateway tee and the adapter's streaming path each see a trailing
    all-zero housekeeping report after a real one and must not let it
    displace the real report and zero out the turn's billing.
    """
    return (
        read_count(usage_obj, "prompt_tokens")
        + read_count(usage_obj, "completion_tokens")
        > 0
    )


def should_replace_usage_report(current: Any, candidate: Any) -> bool:
    """Decide whether ``candidate`` should replace ``current`` as the stored usage report.

    Shared tie-breaking rule for both OpenAI-wire usage producers — the
    gateway tee's ``_process_data_line`` and
    ``openai_execution.execute_stream`` — so the pair cannot silently drift
    into different rules for the same wire:

    1. ``candidate`` must be usage-SHAPED to replace anything at all: it must
       expose at least one of the two counter fields (dict or
       attribute-bearing, via :func:`read_field`). This rejects a malformed
       non-usage value before it is ever stored and later handed to a
       splitter that expects a dict-like or attribute-bearing usage object —
       a JSON-parsed ``"usage": "n/a"`` or ``"usage": 7`` on the gateway
       wire, for instance.
    2. The first usage-shaped report seen is always promoted — nothing
       earlier to protect.
    3. Once a REAL report (non-zero prompt+completion total) is stored, a
       later report only replaces it when it is itself real — a trailing
       all-zero housekeeping report must not overwrite a real one and zero
       out the request's billing.

    Args:
        current: the usage report currently stored/promoted, or ``None``.
        candidate: the newly observed usage report (dict or attribute
            object) to consider promoting in its place.
    """
    if not candidate:
        return False
    if (
        read_field(candidate, "prompt_tokens") is None
        and read_field(candidate, "completion_tokens") is None
    ):
        return False
    if current is None:
        return True
    return _is_real_usage_report(candidate)


def extract_openrouter_cost(payload: Any) -> dict:
    """Pull OpenRouter's own reported cost and serving provider out of a response.

    OpenRouter serves one model id from many provider endpoints whose prices
    differ — up to 3.26x on models we've measured — so a catalog price is
    only an ESTIMATE; the authoritative number is what OpenRouter reports it
    charged. Requires ``usage: {"include": true}`` on the request. Captured on
    every OpenRouter response and used by ``billing_reconcile`` to bill the
    request at the upstream cost (the catalog price is only an estimate).

    Accepts either a dict (the gateway tee's JSON-parsed payload) or an
    attribute-bearing object (the ``synthetic_brain`` adapter's raw openai-SDK
    response/chunk) via :func:`read_field` — the openai package's pydantic
    models are configured with ``extra="allow"``, so OpenRouter's extra
    ``usage.cost``/``provider`` fields survive SDK parsing as ordinary
    attributes. One extractor serves both wire shapes so neither producer can
    silently drift from the other.

    Returns:
        A dict with ``upstream_cost_micros`` and/or ``serving_provider`` when
        present; empty when the response carries neither.
    """
    out: dict = {}
    usage = read_field(payload, "usage")

    cost = read_field(usage, "cost")
    if cost is not None:
        try:
            # OpenRouter reports cost in USD; the ledger stores micro-USD.
            out["upstream_cost_micros"] = Decimal(str(cost)) * _USD_TO_MICROS
        except (ArithmeticError, ValueError, TypeError):
            logger.warning("openrouter usage.cost was not numeric; ignoring")

    # The serving endpoint is reported at the top level, not inside usage.
    serving = read_field(payload, "provider")
    if isinstance(serving, str) and serving:
        out["serving_provider"] = serving

    return out
