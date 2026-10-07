"""Bounded public results; raw credential effects never enter conversation state."""

import json

SECRET_KEYS = {
    "api_key",
    "client_secret",
    "access_token",
    "refresh_token",
    "password",
    "private_key",
    "passphrase",
}


def strip_volatile(value):
    if isinstance(value, list):
        return [strip_volatile(x) for x in value]
    if isinstance(value, dict):
        return {
            k: strip_volatile(v)
            for k, v in value.items()
            if k not in SECRET_KEYS and k not in {"__effects__", "encrypted_content"}
        }
    return value


def bounded(value, limit=100_000):
    value = strip_volatile(value)
    raw = json.dumps(value, default=str, ensure_ascii=False)
    return (
        value
        if len(raw.encode()) <= limit
        else {"truncated": True, "summary": raw[: limit // 2]}
    )


def event_payload(kind, payload):
    """Bound tool bodies without discarding lifecycle identity or verdicts."""
    if kind not in ("tool_begin", "tool_end"):
        return bounded(payload)
    payload = restore_tool_identity(kind, payload)
    result = {}
    for key, value in strip_volatile(payload).items():
        if key in ("args", "result"):
            body = bounded(value, limit=24_000)
            if (
                isinstance(body, dict)
                and body.get("truncated")
                and isinstance(value, dict)
            ):
                # A large error must not turn into a successful tool card.
                for verdict in ("error", "success"):
                    if verdict in value:
                        body[verdict] = bounded(value[verdict], limit=2_000)
            result[key] = body
        else:
            result[key] = bounded(value, limit=2_000)
    return result


def restore_tool_identity(kind, payload):
    """Recover the header from old truncated JSON without guessing a matching call.

    Decode only complete top-level fields preceding args/result. Never search
    nested inspection output for an ID, since it can contain other executions.
    This is a read projection; original events remain unchanged in PostgreSQL.
    """
    if kind not in ("tool_begin", "tool_end") or not payload.get("truncated"):
        return payload
    raw = payload.get("summary")
    if not isinstance(raw, str) or not raw.startswith("{"):
        return payload
    decoder, header, pos = json.JSONDecoder(), {}, 1
    try:
        while pos < len(raw):
            pos += len(raw[pos:]) - len(raw[pos:].lstrip())
            key, pos = decoder.raw_decode(raw, pos)
            pos += len(raw[pos:]) - len(raw[pos:].lstrip())
            if raw[pos] != ":":
                return payload
            pos += 1
            pos += len(raw[pos:]) - len(raw[pos:].lstrip())
            if key in ("args", "result"):
                break
            value, pos = decoder.raw_decode(raw, pos)
            header[key] = value
            pos += len(raw[pos:]) - len(raw[pos:].lstrip())
            if raw[pos] != ",":
                return payload
            pos += 1
    except (ValueError, IndexError, TypeError):
        return payload
    if header.get("event") != kind or not all(
        isinstance(header.get(k), str) and header[k]
        for k in ("tool_call_id", "tool_name")
    ):
        return payload
    return {
        **{k: header[k] for k in ("event", "tool_call_id", "tool_name")},
        "result" if kind == "tool_end" else "args": payload,
    }
