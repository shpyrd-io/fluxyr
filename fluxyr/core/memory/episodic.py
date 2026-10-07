"""Explicit episodic long-term memory for experiences and events."""

from datetime import datetime
from typing import Any

from fluxyr.core.memory.base import MemoryItem


class EpisodicMemory:
    """Explicit episodic long-term memory for experiences and events"""

    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.items = []  # List[MemoryItem]
        self.metadata = {"last_consolidated": None, "consolidation_count": 0}

    def add(
        self,
        content: Any,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add an episodic memory"""
        memory_item = MemoryItem(
            content=content, importance=importance, metadata=metadata
        )
        self.items.append(memory_item)
        self._prune()
        return memory_item.id

    def get(self, memory_id: str) -> Any | None:
        """Get an episodic memory by ID"""
        for item in self.items:
            if item.id == memory_id:
                item.access()
                return item.content
        return None

    def get_all(self) -> list[dict[str, Any]]:
        """Get all episodic memories"""
        return [
            {"id": item.id, "content": item.content, "created_at": item.created_at}
            for item in self.items
        ]

    def search(self, query: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
        """Search episodic memories based on metadata query"""
        # Simple implementation - in real system would use semantic search
        results = []

        for item in self.items:
            match = True
            for key, value in query.items():
                if key in item.metadata and item.metadata[key] != value:
                    match = False
                    break

            if match:
                results.append(
                    {
                        "id": item.id,
                        "content": item.content,
                        "created_at": item.created_at,
                    }
                )

                if len(results) >= top_k:
                    break

        return results

    def _prune(self) -> None:
        """Prune memories when capacity is exceeded"""
        if len(self.items) <= self.capacity:
            return

        # Sort by importance and recency
        sorted_items = sorted(
            self.items, key=lambda x: (x.importance, x.last_accessed), reverse=True
        )

        # Keep only the top items
        self.items = sorted_items[: self.capacity]

    def consolidate(self) -> None:
        """Consolidate episodic memory (cluster related experiences, etc.)"""
        # In a real implementation, this would analyze and reorganize episodic memories
        self.metadata["last_consolidated"] = datetime.now().isoformat()
        self.metadata["consolidation_count"] += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {
            "capacity": self.capacity,
            "items": [item.to_dict() for item in self.items],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodicMemory":
        """Create from dictionary"""
        memory = cls(capacity=data["capacity"])
        memory.metadata = data["metadata"]
        memory.items = [MemoryItem.from_dict(item_data) for item_data in data["items"]]
        return memory
