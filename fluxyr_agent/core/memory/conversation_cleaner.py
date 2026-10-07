"""Utilities for cleaning file attachments from conversation history."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def strip_file_attachments(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace file and image content blocks with lightweight text placeholders.

    Intended for use before memory-analysis calls where large binary payloads
    would waste tokens.  The list is mutated in place — callers must pass a
    deep-copied list if the original must be preserved.

    Args:
        conversation: A (deep-copied) conversation list.

    Returns:
        The same list with file/image items replaced by text stubs.
    """
    files_removed = 0

    for message in conversation:
        if message.get("role") != "user" or "content" not in message:
            continue

        content = message.get("content", [])
        if not isinstance(content, list):
            continue

        new_content: list[dict[str, Any]] = []
        modified = False

        for item in content:
            content_type = item.get("content_type", item.get("type", ""))

            if content_type == "file":
                if "file_name" in item:
                    file_name = item.get("file_name", "unknown")
                elif "file" in item and isinstance(item["file"], dict):
                    file_name = item["file"].get("filename", "unknown")
                else:
                    file_name = "unknown"

                new_content.append(
                    {
                        "type": "text",
                        "text": f"[File attachment: {file_name} - content removed for memory analysis]",
                    }
                )
                files_removed += 1
                modified = True

            elif content_type in ("image", "image_url"):
                new_content.append(
                    {
                        "type": "text",
                        "text": "[Image attachment - content removed for memory analysis]",
                    }
                )
                files_removed += 1
                modified = True

            elif content_type == "file_ref":
                file_name = item.get("filename", item.get("file_id", "unknown"))
                new_content.append(
                    {
                        "type": "text",
                        "text": f"[File attachment: {file_name} - content removed for memory analysis]",
                    }
                )
                files_removed += 1
                modified = True

            elif content_type == "document":
                file_name = item.get("file_name", "unknown")
                new_content.append(
                    {
                        "type": "text",
                        "text": f"[Document attachment: {file_name} - content removed for memory analysis]",
                    }
                )
                files_removed += 1
                modified = True

            else:
                new_content.append(item)

        if modified:
            message["content"] = new_content

    if files_removed > 0:
        logger.debug(
            "Memory analysis: Replaced %d files/images with text references",
            files_removed,
        )

    return conversation
