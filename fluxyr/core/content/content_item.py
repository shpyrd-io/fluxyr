"""Content item classes for representing different types of content in messages."""

import base64
from typing import Any


class ContentItem:
    """Base class for all content items in a message"""

    def __init__(self, content_type: str, metadata: dict[str, Any] | None = None):
        self.content_type = content_type
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dictionary"""
        return {"content_type": self.content_type, "metadata": self.metadata}


class TextContent(ContentItem):
    """Text content in a message"""

    def __init__(self, text: str, metadata: dict[str, Any] | None = None):
        super().__init__("text", metadata)
        self.text = text

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result["text"] = self.text
        return result


class ImageContent(ContentItem):
    """Image content in a message"""

    def __init__(
        self,
        image_data: str | bytes,
        mime_type: str = "image/jpeg",
        is_url: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__("image", metadata)
        self.image_data = image_data
        self.mime_type = mime_type
        self.is_url = is_url

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if self.is_url:
            result["image_data"] = self.image_data
        else:
            if isinstance(self.image_data, bytes):
                result["image_data"] = base64.b64encode(self.image_data).decode("utf-8")
            else:
                result["image_data"] = self.image_data
        result["mime_type"] = self.mime_type
        result["is_url"] = self.is_url
        return result


class DocumentContent(ContentItem):
    """Document content (PDF, Office files) sent as Anthropic document blocks."""

    def __init__(
        self,
        data: str | bytes,
        file_name: str,
        mime_type: str = "application/pdf",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__("document", metadata)
        self.data = data
        self.file_name = file_name
        self.mime_type = mime_type

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if isinstance(self.data, bytes):
            result["data"] = base64.b64encode(self.data).decode("utf-8")
        else:
            result["data"] = self.data
        result["file_name"] = self.file_name
        result["mime_type"] = self.mime_type
        return result


class FileContent(ContentItem):
    """File content in a message"""

    def __init__(
        self,
        file_data: str | bytes,
        file_name: str,
        mime_type: str = "application/octet-stream",
        is_path: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__("file", metadata)
        self.file_data = file_data
        self.file_name = file_name
        self.mime_type = mime_type
        self.is_path = is_path

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if self.is_path:
            result["file_path"] = self.file_data
        else:
            if isinstance(self.file_data, bytes):
                result["file_data"] = base64.b64encode(self.file_data).decode("utf-8")
            else:
                result["file_data"] = self.file_data
        result["file_name"] = self.file_name
        result["mime_type"] = self.mime_type
        result["is_path"] = self.is_path
        return result
