"""Copied into action environments as `fluxyr`. No backend imports."""

import json
import os
import sys
from pathlib import Path

_payload = json.loads(sys.stdin.read())
params = _payload.get("params", {})
data_dir = Path(os.environ["FLUXYR_DATA"])
approval_response = _payload.get("approval_response")


def secret(name):
    """Read a declared x-vault parameter binding or a legacy fixed Vault name."""
    if name not in _payload.get("secrets", {}):
        raise KeyError(
            f"Secret {name!r} was not declared by this action or its Vault parameter was not supplied"
        )
    return _payload["secrets"][name]


def output(value):
    print("__FLUXYR_RESULT__" + json.dumps(value), flush=True)


def log(value):
    print(str(value), flush=True)


# Ordered replies are durable per tool invocation, not shared across actions.
from fluxyr_human import MAX_ROUNDS, normalize_request

_responses = (_payload.get("continuation") or {}).get("responses", [])
_seen_keys = set()
_legacy_used = False


def _interact(variant, title, *, key, context=None, **fields):
    global _legacy_used
    if key in _seen_keys:
        raise ValueError("Use a distinct stable key for each human interaction")
    _seen_keys.add(key)
    for item in _responses:
        if item["request"]["key"] == key:
            if item["request"]["variant"] != variant:
                raise ValueError("Interaction type changed during continuation")
            return item["response"]
    # Legacy callers can still supply one answer directly to runner.run/test.
    if not _responses and approval_response is not None and not _legacy_used:
        _legacy_used = True
        if variant == "continue":
            return {
                "decision": "complete",
                "user_input": approval_response.get("answer", ""),
                "context": context or {},
            }
    if len(_responses) >= MAX_ROUNDS:
        raise ValueError(f"An action supports at most {MAX_ROUNDS} human interactions")
    request = normalize_request(
        {
            "variant": variant,
            "title": title,
            "key": key,
            "context": context or {},
            **fields,
        }
    )
    encoded = json.dumps({"request": request, "responses": _responses})
    if len(encoded) > 80000:
        raise ValueError(
            "Human continuation exceeds 80 KB; keep large artifacts in data_dir"
        )
    print("__FLUXYR_WAIT__" + encoded, flush=True)
    sys.exit(75)


def request_input(title, *, key, placeholder=None, context=None):
    """Return {decision, user_input, context}, or {decision: rejected, reason}."""
    return _interact(
        "continue", title, key=key, placeholder=placeholder, context=context
    )


def request_choice(title, candidates, *, key, message=None, context=None):
    """Return the ORIGINAL selected {index, label, content/path/value, context}."""
    return _interact(
        "choices", title, key=key, choices=candidates, message=message, context=context
    )


def request_confirmation(title, *, key, message=None, context=None):
    """Return {decision: approve/rejected, context}; handle rejection explicitly."""
    return _interact("confirm-reject", title, key=key, message=message, context=context)


def request_human(question):
    """Compatibility shorthand for a free-text interaction, returning {answer}."""
    reply = _interact(
        "continue",
        str(question)[:100],
        key=f"question_{len(_seen_keys) + 1}",
        message=str(question)[:500],
    )
    if reply["decision"] == "rejected":
        return reply
    return {"answer": reply["user_input"]}
