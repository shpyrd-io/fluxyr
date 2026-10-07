"""UserMessage class for representing a complete user message with multiple content items."""

from typing import Any

from fluxyr.core.content.content_item import ContentItem


class UserMessage:
    """Complete user message with multiple content items"""

    def __init__(self, content: list[ContentItem]):
        self.content = content

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format for AI provider messages"""
        return {"role": "user", "content": [item.to_dict() for item in self.content]}
