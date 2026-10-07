"""Base memory item class used by different memory types."""

import uuid
from datetime import datetime
from typing import Any


class MemoryItem:
    """Base class for all memory items"""

    def __init__(
        self,
        content: Any,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ):
        self.id = str(uuid.uuid4())
        self.content = content
        self.importance = importance
        self.metadata = metadata or {}
        self.created_at = datetime.now().isoformat()
        self.last_accessed = self.created_at
        self.access_count = 0

    def access(self) -> None:
        """Track memory access"""
        self.access_count += 1
        self.last_accessed = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {
            "id": self.id,
            "content": self.content,
            "importance": self.importance,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
        """Create from dictionary"""
        instance = cls(
            content=data["content"],
            importance=data["importance"],
            metadata=data["metadata"],
        )
        instance.id = data["id"]
        instance.created_at = data["created_at"]
        instance.last_accessed = data["last_accessed"]
        instance.access_count = data["access_count"]
        return instance
