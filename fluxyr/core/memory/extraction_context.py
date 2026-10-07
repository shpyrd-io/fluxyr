"""Bound extraction input without dropping the user's opening request."""

import json


def compact(value):
    if isinstance(value, dict):
        return {
            k: ("[code omitted]" if k in ("source", "source_code") else compact(v))
            for k, v in value.items()
            if k not in ("signature", "thinking", "reasoning_details")
        }
    if isinstance(value, list):
        return [
            compact(v)
            for v in value[:30]
            if not isinstance(v, dict)
            or v.get("type") not in ("thinking", "redacted_thinking")
        ]
    return value


def clipped(value, limit):
    if not isinstance(value, str):
        value = json.dumps(compact(value), ensure_ascii=False)
    else:
        try:
            value = json.dumps(compact(json.loads(value)), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    if limit <= 20:
        return value[:limit]
    return value if len(value) <= limit else value[: limit - 20] + "\n[truncated]"


def turn_messages(prompt, state):
    items = state.get("short_term", {}).get("items", [])
    opening = max(
        (i for i, m in enumerate(items) if m.get("role") == "user"), default=-1
    )
    turn = items[opening + 1 :]
    result = [{"role": "user", "content": clipped(prompt, 6000)}]
    budget = 6000
    # Reserve space for the final answer independently of intermediate tool traffic.
    for m in turn[:-1]:
        if m.get("role") == "system" or budget <= 0:
            continue
        value = clipped(m.get("content", ""), min(1200, budget))
        result.append({"role": m.get("role", "tool"), "content": value})
        budget -= len(value)
    if turn:
        result.append(
            {
                "role": turn[-1].get("role", "assistant"),
                "content": clipped(turn[-1].get("content", ""), 4000),
            }
        )
    return result


def bounded_memories(semantic, episodic, messages):
    query = json.dumps(messages, ensure_ascii=False).casefold()
    ordered = sorted(
        semantic.items(),
        key=lambda pair: (str(pair[0]).casefold() not in query, pair[0]),
    )
    selected, budget = {}, 9000
    for key, value in ordered:
        item = clipped(value, 2000)
        cost = len(str(key)) + len(item)
        if cost <= budget:
            selected[key] = item
            budget -= cost
    episodes, budget = [], 3000
    for item in episodic:
        value = clipped(item.get("content", ""), 1000)
        if len(value) <= budget:
            episodes.append({"id": item["id"], "content": value})
            budget -= len(value)
    return selected, episodes
