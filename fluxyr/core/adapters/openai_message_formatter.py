"""Message and file-content formatting helpers for the OpenAI Chat API adapter."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

DOCUMENT_ON_OPENAI_STUB = (
    "[Document attachment: {filename} — documents cannot be sent to this "
    "model. Save it under data/ and extract its text with local tools to inspect it.]"
)

# Logged once per (adapter, type) per process — see the Anthropic formatter for
# why one line per block per iteration is worse than useless.
_WARNED_BLOCK_TYPES: set = set()


def _warn_once(adapter: str, content_type: str) -> None:
    """Log an unknown content block the first time this process sees it."""
    key = (adapter, content_type)
    if key in _WARNED_BLOCK_TYPES:
        return
    _WARNED_BLOCK_TYPES.add(key)
    logger.warning(
        "%s formatter: unsupported content block %r — emitted as a visible stub",
        adapter,
        content_type,
    )


def format_file_content(item: dict[str, Any]) -> dict[str, Any]:
    """Convert an internal file content item to the OpenAI Chat API format.

    Handles both original ``file_data``/``file_name`` format (from
    :class:`~fluxyr.core.content.content_item.FileContent`)
    and the pre-formatted OpenAI ``file`` dict format.  Images are sent as
    ``image_url`` blocks; other file types are sent as ``file`` blocks.

    Args:
        item: A content item dict representing a file.

    Returns:
        A content part dict ready for inclusion in an OpenAI API message.
    """
    if "file_data" in item and "file_name" in item:
        file_data = item.get("file_data", "")
        file_name = item.get("file_name", "unknown")
        mime_type = item.get("mime_type", "application/octet-stream")

        if mime_type.startswith("image/"):
            if isinstance(file_data, str) and file_data.startswith("data:"):
                data_url = file_data
            else:
                data_url = f"data:{mime_type};base64,{file_data}"
            return {"type": "image_url", "image_url": {"url": data_url}}

        if isinstance(file_data, str) and file_data.startswith("data:"):
            data_url = file_data
        else:
            data_url = f"data:{mime_type};base64,{file_data}"

    elif "file" in item and isinstance(item["file"], dict):
        file_name = item["file"].get("filename", "unknown")
        data_url = item["file"].get("file_data", "")
        if data_url.startswith("data:image/"):
            return {"type": "image_url", "image_url": {"url": data_url}}

    else:
        return {"type": "text", "text": "[Unrecognized file format]"}

    return {"type": "file", "file": {"filename": file_name, "file_data": data_url}}


def _format_user_content(content: Any) -> Any:
    """Format the content field of a user message for the OpenAI API."""
    if not isinstance(content, list):
        return content

    parts: list[dict[str, Any]] = []
    for content_item in content:
        content_type = content_item.get("content_type", content_item.get("type", ""))

        if content_type == "text" or content_item.get("type") == "text":
            parts.append({"type": "text", "text": content_item.get("text", "")})

        elif content_type == "image" or content_item.get("type") == "image_url":
            if (
                content_item.get("is_url", False)
                or content_item.get("type") == "image_url"
            ):
                image_url = content_item.get("image_data", "")
                if (
                    content_item.get("type") == "image_url"
                    and "image_url" in content_item
                ):
                    image_url = content_item["image_url"].get("url", "")
                parts.append({"type": "image_url", "image_url": {"url": image_url}})
            else:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{content_item.get('mime_type', 'image/jpeg')};base64,"
                                f"{content_item.get('image_data', '')}"
                            )
                        },
                    }
                )

        elif content_type == "file" or content_item.get("type") == "file":
            parts.append(format_file_content(content_item))

        elif content_type == "document":
            # Documents have never reached this wire — this formatter has no
            # document branch and never has. A NAMED stub rather than the
            # unknown-block catch-all below, because this shape is expected:
            # routing it through _warn_once would log a known, accepted gap on
            # every one of up to 25 iterations per turn.
            parts.append(
                {
                    "type": "text",
                    "text": DOCUMENT_ON_OPENAI_STUB.format(
                        filename=content_item.get("file_name", "document"),
                    ),
                }
            )

        else:
            # Closed set. This formatter previously had NO else branch, so an
            # unrecognised block was dropped without trace — and a message whose
            # only block was unrecognised formatted to content: [], which the
            # API rejects with a 400 that names nothing useful.
            _warn_once("openai", content_type)
            parts.append(
                {
                    "type": "text",
                    "text": f"[Unsupported content block: {content_type}]",
                }
            )

    if not parts:
        # A user message must never reach the wire with empty content. Guarding
        # the OUTPUT covers an empty input list too.
        parts = [{"type": "text", "text": "[Attachment content unavailable]"}]
    return parts


def format_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Format an internal message list for the OpenAI Chat API.

    Handles system, user, assistant (with and without tool calls), tool, and
    legacy ``function_call_output`` message formats.

    Args:
        messages: Raw internal message list (as stored in
            :class:`~fluxyr.core.memory.short_term.ShortTermMemory`).

    Returns:
        Message list suitable for ``client.chat.completions.create()``.
    """
    formatted: list[dict[str, Any]] = []
    tool_call_ids: dict[str, int] = {}
    pending_images: list[dict[str, Any]] = []

    # First pass: collect all tool call IDs from assistant messages so we can
    # filter orphaned tool-result messages in the second pass.
    for idx, message in enumerate(messages):
        if message.get("role") == "assistant" and "function_calls" in message:
            for fc in message["function_calls"]:
                call_id = fc.get("call_id", "")
                if call_id:
                    tool_call_ids[call_id] = idx

    for message in messages:
        role = message.get("role", "")
        if (
            role != "tool"
            and message.get("type") != "function_call_output"
            and pending_images
        ):
            formatted.append(
                {"role": "user", "content": _format_user_content(pending_images)}
            )
            pending_images = []

        if role == "system":
            formatted.append({"role": "system", "content": message.get("content", "")})

        elif role == "user":
            formatted.append(
                {
                    "role": "user",
                    "content": _format_user_content(message.get("content", "")),
                }
            )

        elif role == "assistant" and "function_calls" not in message:
            formatted.append(
                {"role": "assistant", "content": message.get("content", "")}
            )

        elif role == "assistant" and "function_calls" in message:
            content = message.get("content", "") or None  # Chat API accepts null
            tool_calls = [
                {
                    "id": fc.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("arguments", {})),
                    },
                }
                for fc in message["function_calls"]
            ]
            formatted.append(
                {"role": "assistant", "content": content, "tool_calls": tool_calls}
            )

        elif role == "tool":
            tool_call_id = message.get("tool_call_id", "")
            if tool_call_id and tool_call_id in tool_call_ids:
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": message.get("name", "unknown_tool"),
                        "content": message.get("content", "{}"),
                    }
                )

                if message.get("attachments"):
                    pending_images.append(
                        {
                            "type": "text",
                            "text": f"Image returned by {message.get('name', 'read')} (call {tool_call_id}):",
                        }
                    )
                    pending_images.extend(message["attachments"])

        elif message.get("type") == "function_call_output":
            call_id = message.get("call_id", "")
            if call_id and call_id in tool_call_ids:
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": message.get("name", "unknown_tool"),
                        "content": message.get("output", "{}"),
                    }
                )

    if pending_images:
        formatted.append(
            {"role": "user", "content": _format_user_content(pending_images)}
        )
    return formatted
