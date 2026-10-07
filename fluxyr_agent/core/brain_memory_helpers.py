"""Helpers for cleaning large file attachments from short-term memory."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clean_large_files_from_memory(
    messages: list[dict[str, Any]],
    large_file_threshold_mb: float,
) -> int:
    """Remove or stub-out large file attachments from a list of messages.

    Tracks the total size of all base64-encoded files in the message list,
    prioritises removing the largest files first, and ensures the total size
    stays below OpenAI's 32 MB per-request limit.  Individual files that
    exceed ``large_file_threshold_mb`` are also replaced even when the total
    is within the limit.

    The ``messages`` list is mutated in place.

    Args:
        messages: The raw message list from ``ShortTermMemory.get_all()``.
        large_file_threshold_mb: Per-file size threshold in megabytes above
            which a file is always removed.

    Returns:
        Number of file items replaced with text placeholders.
    """
    files_removed = 0
    max_total_size_bytes = 32 * 1024 * 1024  # 32 MB OpenAI limit
    default_threshold_bytes = large_file_threshold_mb * 1024 * 1024

    # Collect (msg_idx, content_idx, item, size, file_name, mime_type) tuples
    all_files = []

    for msg_idx, message in enumerate(messages):
        if message.get("role") != "user" or "content" not in message:
            continue

        content = message.get("content", [])
        if not isinstance(content, list):
            continue

        for content_idx, item in enumerate(content):
            content_type = item.get("content_type", "")
            if content_type != "file":
                continue

            file_data = item.get("file_data", "")
            file_name = item.get("file_name", "unknown")
            mime_type = item.get("mime_type", "application/octet-stream")

            if not file_data or not isinstance(file_data, str):
                continue

            if not item.get("is_path", False):
                # Rough estimate: base64 encodes 3 bytes per 4 chars
                estimated_size = len(file_data) * 3 // 4
                all_files.append(
                    (msg_idx, content_idx, item, estimated_size, file_name, mime_type)
                )

    if not all_files:
        return 0

    total_size = sum(f[3] for f in all_files)
    logger.debug(
        "Total size of all files in memory: %.2f MB", total_size / (1024 * 1024)
    )

    def _replace(
        msg_idx: int, content_idx: int, size: int, file_name: str, mime_type: str
    ) -> None:
        messages[msg_idx]["content"][content_idx] = {
            "content_type": "text",
            "text": (
                f"[Large file: {file_name} ({mime_type}) - "
                f"{size / (1024 * 1024):.2f} MB - removed from memory to reduce token usage]"
            ),
        }

    if total_size <= max_total_size_bytes:
        # Only remove individual files that exceed the per-file threshold
        for msg_idx, content_idx, _item, size, file_name, mime_type in all_files:
            if size > default_threshold_bytes:
                _replace(msg_idx, content_idx, size, file_name, mime_type)
                files_removed += 1
        return files_removed

    # Total is over 32 MB — remove largest files first until under the limit
    logger.debug(
        "Need to clean files to get under the 32 MB limit. Current size: %.2f MB",
        total_size / (1024 * 1024),
    )
    all_files.sort(key=lambda x: x[3], reverse=True)

    running_total = total_size
    for msg_idx, content_idx, _item, size, file_name, mime_type in all_files:
        if running_total > max_total_size_bytes:
            _replace(msg_idx, content_idx, size, file_name, mime_type)
            files_removed += 1
            running_total -= size
            if running_total <= max_total_size_bytes:
                break

    logger.debug(
        "Removed %d files. New total size: %.2f MB",
        files_removed,
        running_total / (1024 * 1024),
    )
    return files_removed
