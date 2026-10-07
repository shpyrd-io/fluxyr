"""Level-2 shrink passes for techdoc bundles.

These passes are opt-in (level 2) and quality-risky: they modify structured
content (JSON schemas, code samples) in ways that could affect completeness.

This module is internal to the techdoc.shrink package — import from shrink.py,
not here.
"""

from __future__ import annotations

import json
import re

# ── Constants ────────────────────────────────────────────────────────────────

# Fenced blocks longer than this (body lines, excluding delimiters) are
# candidates for JSON collapse.
_JSON_COLLAPSE_LINES = 120

# Require at least this many consecutive same-section blocks to collapse.
_MIN_SAMPLE_GROUP = 3

# Language preference order for code sample collapsing.
_SAMPLE_LANG_PREFERENCE = [
    "curl",
    "bash",
    "shell",
    "python",
    "javascript",
    "node",
    "js",
]

# Languages that are considered "equivalent sample candidates".
_SAMPLE_LANGS = {
    "curl",
    "bash",
    "shell",
    "python",
    "javascript",
    "node",
    "js",
    "ruby",
    "go",
    "java",
    "php",
    "typescript",
    "ts",
}


# ── _collapse_giant_json ──────────────────────────────────────────────────────


def collapse_giant_json(markdown: str) -> str:
    """Collapse oversized fenced JSON blocks to depth-2 structure.

    Fenced blocks tagged `json` (or untagged but starting with `{` or `[`) that
    are longer than _JSON_COLLAPSE_LINES lines are parsed, truncated to depth 2,
    and re-serialised.  If json.loads fails the block is left untouched.
    A comment marker is appended after the closing fence delimiter.
    """
    lines = markdown.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    fence_re = re.compile(r"^(\s*)(```|~~~)(.*)")
    in_fence = False
    fence_marker: str = ""
    fence_lang: str = ""
    fence_lines: list[str] = []

    while i < len(lines):
        line = lines[i]
        m = fence_re.match(line.rstrip("\n\r"))
        if m and not in_fence:
            in_fence = True
            fence_marker = m.group(2)
            fence_lang = m.group(3).strip().lower()
            fence_lines = [line]
            i += 1
            continue

        if in_fence:
            close_re = re.compile(r"^\s*" + re.escape(fence_marker) + r"\s*$")
            if close_re.match(line.rstrip("\n\r")):
                fence_lines.append(line)
                in_fence = False
                body_lines = fence_lines[1:-1]

                if len(body_lines) > _JSON_COLLAPSE_LINES:
                    body_text = "".join(body_lines).strip()
                    is_json_tagged = fence_lang == "json"
                    is_json_like = (
                        (not fence_lang) and body_text and body_text[0] in ("{", "[")
                    )

                    if is_json_tagged or is_json_like:
                        try:
                            parsed = json.loads(body_text)
                            collapsed = _truncate_depth(parsed, max_depth=2)
                            new_body = json.dumps(collapsed, indent=2)
                            new_block = (
                                fence_lines[0]
                                + new_body
                                + "\n"
                                + fence_lines[-1]
                                + "<!-- collapsed: full schema truncated to depth 2 -->\n"
                            )
                            result.append(new_block)
                            i += 1
                            continue
                        except (json.JSONDecodeError, ValueError):
                            pass  # Leave untouched on parse failure.

                result.extend(fence_lines)
                i += 1
                continue
            else:
                fence_lines.append(line)
                i += 1
                continue

        result.append(line)
        i += 1

    if in_fence:
        result.extend(fence_lines)

    return "".join(result)


def _truncate_depth(obj, max_depth: int, current_depth: int = 0):
    """Recursively truncate a JSON-parsed object to *max_depth* levels."""
    if current_depth >= max_depth:
        if isinstance(obj, dict):
            return {"...": ""}
        if isinstance(obj, list):
            return ["..."]
        return obj

    if isinstance(obj, dict):
        return {
            k: _truncate_depth(v, max_depth, current_depth + 1) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_truncate_depth(item, max_depth, current_depth + 1) for item in obj]
    return obj


# ── _collapse_code_samples ────────────────────────────────────────────────────


def _blocks_are_plausibly_equivalent(blocks: list[dict]) -> bool:
    """Return True if a group of code blocks is plausibly the same sample.

    Two criteria must both hold:

    1. **Similar line counts**: the ratio of the longest to shortest body (lines
       excluding fence delimiters) must be <= 3.  This prevents collapsing an
       install snippet (3 lines) with a full SDK example (60 lines).

    2. **Shared significant token**: at least one token of length >= 6 must appear
       in ALL blocks.  Tokens are extracted by splitting on whitespace and stripping
       common punctuation.  A shared URL path, function name, or API method name
       satisfies this — random install commands do not share any such token.
    """
    if len(blocks) < 2:
        return True

    # --- Criterion 1: line count ratio ---
    def _body_lines(block: dict) -> int:
        body = block["content"].splitlines()
        # First and last lines are fence delimiters; body is everything between.
        return max(0, len(body) - 2)

    counts = [_body_lines(b) for b in blocks]
    max_c, min_c = max(counts), min(counts)
    # Treat empty blocks as 1 to avoid division by zero.
    if max_c / max(min_c, 1) > 3:
        return False

    # --- Criterion 2: shared significant token ---
    _PUNCT_RE = re.compile(r"[^\w/.-]")

    def _tokens(block: dict) -> set[str]:
        raw = _PUNCT_RE.sub(" ", block["content"])
        return {t for t in raw.split() if len(t) >= 6}

    token_sets = [_tokens(b) for b in blocks]
    common = token_sets[0]
    for ts in token_sets[1:]:
        common = common & ts
    return bool(common)


def collapse_code_samples(markdown: str) -> str:
    """Collapse groups of >= 3 consecutive equivalent code samples to one block.

    Under the same heading section, consecutive fenced blocks with different
    language tags (curl/python/javascript/ruby/go/java/php/bash/shell) that are
    plausibly the same sample are collapsed: one block is kept (by preference
    order) and the rest are dropped with a count marker appended.

    "Plausibly equivalent" requires BOTH:
      - Similar body line counts: max/min ratio <= 3.
      - At least one significant token (length >= 6) shared by all blocks in the
        group.  A common URL path, function name, or API call name qualifies;
        unrelated snippets (install / configure / run) will not share one.

    Blocks with identical language tags are not collapsed.
    Non-consecutive blocks (separated by substantive text) are kept as-is.
    """
    lines = markdown.splitlines(keepends=True)
    i = 0
    fence_re = re.compile(r"^(\s*)(```|~~~)([\w+-]*)")
    segments: list[dict] = []

    while i < len(lines):
        line = lines[i]
        m = fence_re.match(line.rstrip("\n\r"))
        if m:
            fence_marker = m.group(2)
            lang = m.group(3).strip().lower()
            fence_content = [line]
            i += 1
            close_re = re.compile(r"^\s*" + re.escape(fence_marker) + r"\s*$")
            while i < len(lines):
                fence_content.append(lines[i])
                if close_re.match(lines[i].rstrip("\n\r")):
                    i += 1
                    break
                i += 1
            segments.append(
                {"type": "fence", "lang": lang, "content": "".join(fence_content)}
            )
        else:
            if segments and segments[-1]["type"] == "text":
                segments[-1]["content"] += line
            else:
                segments.append({"type": "text", "lang": "", "content": line})
            i += 1

    output: list[str] = []
    j = 0
    while j < len(segments):
        seg = segments[j]
        if seg["type"] != "fence" or seg["lang"] not in _SAMPLE_LANGS:
            output.append(seg["content"])
            j += 1
            continue

        group_blocks = [seg]
        group_segs: list[int] = [j]
        k = j + 1
        while k < len(segments):
            if segments[k]["type"] == "text" and _is_separator(segments[k]):
                if (
                    k + 1 < len(segments)
                    and segments[k + 1]["type"] == "fence"
                    and segments[k + 1]["lang"] in _SAMPLE_LANGS
                ):
                    group_segs.append(k)
                    group_segs.append(k + 1)
                    group_blocks.append(segments[k + 1])
                    k += 2
                    continue
            elif (
                segments[k]["type"] == "fence" and segments[k]["lang"] in _SAMPLE_LANGS
            ):
                group_segs.append(k)
                group_blocks.append(segments[k])
                k += 1
                continue
            break

        if len(group_blocks) >= _MIN_SAMPLE_GROUP:
            langs = [b["lang"] for b in group_blocks]
            if len(set(langs)) > 1 and _blocks_are_plausibly_equivalent(group_blocks):
                kept = _choose_preferred(group_blocks)
                removed_count = len(group_blocks) - 1
                output.append(kept["content"])
                output.append(
                    f"<!-- collapsed: {removed_count} equivalent code samples removed -->\n"
                )
                j = group_segs[-1] + 1
                continue

        output.append(seg["content"])
        j += 1

    return "".join(output)


def _is_separator(seg: dict) -> bool:
    """Return True if a text segment is blank or a single short line (<= 80 chars)."""
    text = seg["content"].strip()
    lines_in = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines_in) == 0 or (len(lines_in) == 1 and len(text) <= 80)


def _choose_preferred(blocks: list[dict]) -> dict:
    """Return the preferred block from a group by language preference order."""
    for pref in _SAMPLE_LANG_PREFERENCE:
        for b in blocks:
            if b["lang"] == pref:
                return b
    return blocks[0]
