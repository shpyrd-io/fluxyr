"""Implicit long-term memory for procedural knowledge and patterns."""

from datetime import datetime


class ImplicitMemory:
    """Implicit long-term memory for procedural knowledge and patterns"""

    def __init__(self):
        self.patterns = {}  # Dict[key, Dict[sub_key, count]]
        self.metadata = {
            "last_updated": datetime.now().isoformat(),
            "total_observations": 0,
        }

    def observe(self, pattern_key: str, observation: str) -> None:
        """Record an observation in a pattern"""
        if pattern_key not in self.patterns:
            self.patterns[pattern_key] = {}

        if observation not in self.patterns[pattern_key]:
            self.patterns[pattern_key][observation] = 0

        self.patterns[pattern_key][observation] += 1
        self.metadata["total_observations"] += 1
        self.metadata["last_updated"] = datetime.now().isoformat()

    def get_pattern(self, pattern_key: str, top_k: int = 3) -> list[tuple[str, int]]:
        """Get the most common observations for a pattern"""
        if pattern_key not in self.patterns:
            return []

        # Sort by frequency (descending)
        sorted_observations = sorted(
            self.patterns[pattern_key].items(), key=lambda x: x[1], reverse=True
        )

        return sorted_observations[:top_k]

    def get_all_patterns(self) -> dict[str, dict[str, int]]:
        """Get all patterns"""
        return self.patterns

    def to_dict(self) -> dict:
        """Convert to serializable dictionary"""
        return {"patterns": self.patterns, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: dict) -> "ImplicitMemory":
        """Create from dictionary"""
        memory = cls()
        memory.patterns = data["patterns"]
        memory.metadata = data["metadata"]
        return memory
