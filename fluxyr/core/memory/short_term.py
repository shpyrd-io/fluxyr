"""Short-term working memory with limited capacity."""

import json
from typing import Any

MAX_TOOL_IMAGE_BYTES = 12 * 1024 * 1024


class ShortTermMemory:
    """Short-term working memory with limited capacity"""

    def __init__(self, capacity: int = 10):
        self.capacity = capacity
        self.items = []  # List of messages (Dict[str, Any])

    def add(self, message: dict[str, Any]) -> None:
        """Add a message to short-term memory"""
        self.items.append(message)
        self._prune()

    def _prune(self) -> list[dict[str, Any]]:
        """Prune items when capacity exceeded, return pruned items"""
        # A long tool-only turn may exceed the message capacity before its next
        # user boundary. Bound image payloads independently, keeping newest first.
        image_bytes = 0
        for message in reversed(self.items):
            attachments = message.get("attachments", [])
            size = sum(len(item.get("image_data", "")) for item in attachments)
            if image_bytes + size > MAX_TOOL_IMAGE_BYTES:
                message.pop("attachments", None)
                message["content"] = json.dumps(
                    {
                        "previous_result": message.get("content", ""),
                        "note": "Earlier image omitted to bound context size. Use read to attach it again if needed.",
                    },
                    ensure_ascii=False,
                )
            else:
                image_bytes += size
        if len(self.items) <= self.capacity:
            return []

        # Always keep system messages
        system_messages = [msg for msg in self.items if msg.get("role") == "system"]
        other_messages = [msg for msg in self.items if msg.get("role") != "system"]

        # Calculate how many to keep
        to_keep = self.capacity - len(system_messages)
        if to_keep <= 0:
            # Edge case: keep only system messages
            pruned = other_messages
            self.items = system_messages
            return pruned

        # Trim at a user-turn boundary. Never retain a tool result without
        # the assistant tool call it answers, even when a batch exceeds capacity.
        cutoff = max(0, len(other_messages) - to_keep)
        boundaries = [
            i
            for i, m in enumerate(other_messages)
            if m.get("role") == "user" and i <= cutoff
        ]
        start = boundaries[-1] if boundaries else 0
        pruned = other_messages[:start]
        self.items = system_messages + other_messages[start:]
        return pruned

    def get_all(self) -> list[dict[str, Any]]:
        """Return the internal messages list (mutable reference). Do not modify unless intentional."""
        return self.items

    def clear(self) -> None:
        """Clear all non-system messages"""
        self.items = [msg for msg in self.items if msg.get("role") == "system"]

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {"capacity": self.capacity, "items": self.items}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShortTermMemory":
        """Create from dictionary"""
        memory = cls(capacity=data["capacity"])
        memory.items = data["items"]
        return memory
