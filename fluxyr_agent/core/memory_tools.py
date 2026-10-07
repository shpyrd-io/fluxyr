"""Explicit, read-only memory access alongside separately named mutations."""

import json


def memory_tools(brain):
    def read_memory(memory_type="all", query="", key=None, **_):
        values = {
            "semantic": brain._semantic.get_all(),
            "episodic": brain._episodic.get_all(),
            "implicit": brain.get_implicit_patterns(),
        }
        if memory_type not in (*values, "all"):
            return {"error": "Unknown memory type"}, "continue"
        if key is not None:
            values["semantic"] = {
                k: v for k, v in values["semantic"].items() if k == key
            }
        if memory_type != "all":
            values = {memory_type: values[memory_type]}
        if query:
            q = query.casefold()
            for name, items in values.items():
                values[name] = (
                    {
                        k: v
                        for k, v in items.items()
                        if q in json.dumps([k, v], ensure_ascii=False).casefold()
                    }
                    if isinstance(items, dict)
                    else [
                        v
                        for v in items
                        if q in json.dumps(v, ensure_ascii=False).casefold()
                    ]
                )
        return values, "continue"

    def delete_memory(memory_type, key, **_):
        if memory_type == "semantic":
            removed = brain._semantic.remove(key)
        elif memory_type == "episodic":
            before = len(brain._episodic.items)
            brain._episodic.items = [v for v in brain._episodic.items if v.id != key]
            removed = before != len(brain._episodic.items)
        else:
            return {
                "error": "Deletion supports semantic keys or episodic IDs"
            }, "continue"
        brain._analysis_pending = False
        if brain._memory_changed:
            brain._memory_changed()
        return {"deleted": removed, "key": key}, "continue"

    return [
        {
            "name": "read_memory",
            "description": "Read or search existing saved memories. Never writes. Use this when asked what you remember, to look up facts, or before updating a memory. Query is a case-insensitive text filter; omit it to list all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "enum": ["all", "semantic", "episodic", "implicit"],
                    },
                    "query": {"type": "string"},
                    "key": {"type": "string"},
                },
                "required": [],
            },
            "function": read_memory,
            "parallel_safe": True,
        },
        {
            "name": "delete_memory",
            "description": "Forget a specific saved semantic key or episodic ID after inspecting it with read_memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {"type": "string", "enum": ["semantic", "episodic"]},
                    "key": {"type": "string"},
                },
                "required": ["memory_type", "key"],
            },
            "function": delete_memory,
        },
    ]
