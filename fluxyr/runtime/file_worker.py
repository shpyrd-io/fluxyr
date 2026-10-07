"""Standalone worker: file tools execute behind the same policy as bash/Python."""

import base64
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_edit import replace_blocks

MAX_BYTES = 50 * 1024
MAX_LINES = 2000


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        if path.exists():
            os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as file:
            file.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_image(path):
    from PIL import Image, ImageOps

    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Image exceeds 20 MB; resize it before reading")
    with Image.open(path) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError(
                "Image exceeds 25 million pixels; resize it before reading"
            )
        image = ImageOps.exif_transpose(image)
        image.thumbnail((2000, 2000))
        image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        while buffer.tell() > 3 * 1024 * 1024:
            image.thumbnail((max(1, image.width // 2), max(1, image.height // 2)))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=80)
        return {
            "content": f"Read image {path.name} ({image.width}x{image.height})",
            "image": {
                "content_type": "image",
                "mime_type": "image/jpeg",
                "image_data": base64.b64encode(buffer.getvalue()).decode(),
            },
        }


def read_text(path, offset=1, limit=None):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        raise ValueError("offset must be a positive 1-indexed integer")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise ValueError("limit must be a positive integer")
    maximum = min(limit or MAX_LINES, MAX_LINES)
    output, size, total, more, oversized = [], 0, 0, False, None
    with path.open("rb") as file:
        while True:
            part = file.readline(MAX_BYTES + 1)
            if not part:
                break
            length, line = len(part), part
            while not part.endswith(b"\n"):
                part = file.readline(MAX_BYTES + 1)
                if not part:
                    break
                length += len(part)
            total += 1
            if total < offset:
                continue
            if more:
                continue
            if len(output) >= maximum or size + length > MAX_BYTES:
                more = True
                if not output and length > MAX_BYTES:
                    oversized = total
                continue
            output.append(line.decode("utf-8"))
            size += length
    if offset > max(total, 1):
        raise ValueError(f"Offset {offset} is beyond end of file ({total} lines total)")
    content = "".join(output)
    next_offset = offset + len(output) if more else None
    if oversized:
        content = (
            f"[Line {oversized} exceeds 50 KiB. Use bash to inspect a bounded slice.]"
        )
    elif more:
        content += f"\n\n[Showing lines {offset}-{next_offset - 1} of {total}. Use offset={next_offset} to continue.]"
    return {
        "content": content,
        "truncated": more,
        "next_offset": next_offset,
        "total_lines": total,
    }


def execute(operation, args):
    path = Path(args["path"]).expanduser().resolve()
    if operation == "read":
        if not path.is_file():
            raise ValueError(
                "read requires a regular file; use bash to list directories"
            )
        with path.open("rb") as file:
            head = file.read(16)
        if head.startswith(
            (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")
        ) or (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
            return read_image(path)
        if b"\0" in head:
            raise ValueError(
                "Binary file; use bash to inspect or render_preview to display it"
            )
        return read_text(path, args.get("offset", 1), args.get("limit"))
    if path.exists() and not path.is_file():
        raise ValueError("File path required")
    if operation == "write":
        atomic_write(path, args["content"])
        return {"content": f"Successfully wrote to {args['path']}"}
    if operation == "edit":
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError(
                "File exceeds 8 MiB edit limit; use a streaming transformation with bash"
            )
        raw = path.read_bytes().decode("utf-8")
        content, patch, line = replace_blocks(raw, args["edits"], args["path"])
        atomic_write(path, content)
        return {
            "content": f"Successfully replaced {len(args['edits'])} block(s) in {args['path']}.",
            "diff": patch[:MAX_BYTES],
            "first_changed_line": line,
            "diff_truncated": len(patch) > MAX_BYTES,
        }
    raise ValueError("Unknown file operation")


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        result = execute(request["operation"], request["args"])
    except Exception as exc:  # noqa: BLE001 - standalone worker protocol boundary
        result = {"error": str(exc), "type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
