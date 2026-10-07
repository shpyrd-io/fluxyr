"""Explicit semantic long-term memory for factual knowledge."""

from datetime import datetime
from typing import Any

from fluxyr_agent.core.memory.base import MemoryItem


class SemanticMemory:
    """Explicit semantic long-term memory for factual knowledge"""

    def __init__(self):
        self.items = {}  # Dict[key, MemoryItem]
        self.metadata = {"last_consolidated": None, "consolidation_count": 0}

    def add(
        self,
        key: str,
        content: Any,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add or update an item in semantic memory"""
        if key in self.items:
            # Update existing item
            self.items[key].content = content
            self.items[key].importance = max(self.items[key].importance, importance)
            self.items[key].access()
            if metadata:
                self.items[key].metadata.update(metadata)
            return self.items[key].id
        else:
            # Add new item
            memory_item = MemoryItem(
                content=content, importance=importance, metadata=metadata
            )
            self.items[key] = memory_item
            return memory_item.id

    def get(self, key: str) -> Any | None:
        """Get an item from semantic memory"""
        if key in self.items:
            self.items[key].access()
            return self.items[key].content
        return None

    def get_all(self) -> dict[str, Any]:
        """Get all semantic memories as dictionary of key -> content"""
        return {key: item.content for key, item in self.items.items()}

    def remove(self, key: str) -> bool:
        """Remove an item from semantic memory"""
        if key in self.items:
            del self.items[key]
            return True
        return False

    def consolidate(self) -> None:
        """Consolidate semantic memory (merge related items, etc.)"""
        # In a real implementation, this would analyze and reorganize semantic memories
        self.metadata["last_consolidated"] = datetime.now().isoformat()
        self.metadata["consolidation_count"] += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {
            "items": {key: item.to_dict() for key, item in self.items.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SemanticMemory":
        """Create from dictionary"""
        memory = cls()
        memory.metadata = data["metadata"]
        for key, item_data in data["items"].items():
            memory.items[key] = MemoryItem.from_dict(item_data)
        return memory
