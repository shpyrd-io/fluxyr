"""Deterministic pre-LLM content shrink pass for techdoc bundles.

Reduces token cost before pages reach the LLM by removing redundant content
(images, boilerplate, oversized pages) without altering API-relevant text.

The techdoc package is app-independent: only stdlib is used here.

Configuration env vars (read at call time, not import time):
  TECHDOC_SHRINK_LEVEL  — 0 (identity), 1 (safe default), 2 (opt-in quality-risky).
                          Invalid values warn and default to 1.
  TECHDOC_MAX_PAGE_WORDS — per-page word cap used by _cap_page_words (default 12_000).
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter

from .models import Page
from .shrink_passes import collapse_code_samples as _collapse_code_samples
from .shrink_passes import collapse_giant_json as _collapse_giant_json

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_MAX_PAGE_WORDS = 12_000

# Boilerplate pass thresholds: a line must appear in > this fraction AND >= this count.
_BOILERPLATE_FRACTION = 0.30
_BOILERPLATE_MIN_PAGES = 5
# Only run the boilerplate pass when the bundle has at least this many pages,
# otherwise too many legitimate lines would be removed from small bundles.
_BOILERPLATE_MIN_BUNDLE = 8


# ── Fence-aware line iterator ─────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^(\s*)(```|~~~)")


def _iter_lines_fence_aware(markdown: str):
    """Yield (line, inside_fence, is_fence_delimiter) for every line in *markdown*.

    A fence starts on a line beginning with ``` or ~~~ (leading whitespace
    allowed) and ends when the SAME marker appears again (CommonMark: a ```
    fence can only be closed by ```, not by ~~~, and vice-versa).  Content
    inside a fence is never modified by any pass.

    is_fence_delimiter is True for both opening and closing fence lines.
    The caller must never remove fence delimiter lines.
    """
    in_fence = False
    fence_marker: str = ""  # The opening marker token (``` or ~~~)
    for line in markdown.splitlines(keepends=True):
        stripped = line.rstrip("\n\r")
        m = _FENCE_RE.match(stripped)
        if m:
            marker = m.group(2)
            if not in_fence:
                # Opening delimiter — start a new fence.
                fence_marker = marker
                yield line, False, True
                in_fence = True
            elif marker == fence_marker:
                # Closing delimiter — must match the opening marker.
                yield line, True, True
                in_fence = False
                fence_marker = ""
            else:
                # Wrong marker type inside a fence — treat as regular content.
                yield line, True, False
        else:
            yield line, in_fence, False


# ── Level 1 passes ────────────────────────────────────────────────────────────

_IMAGE_INLINE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_BADGE_LINK_IMAGE_RE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
_HTML_COMMENT_INLINE_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_COMMENT_OPEN_RE = re.compile(r"<!--")
_HTML_COMMENT_CLOSE_RE = re.compile(r"-->")


def _strip_images(markdown: str) -> str:
    """Remove image lines, inline images, badge-style link-images, and HTML comments.

    Pre-existing blank lines (paragraph separators) are preserved unchanged.
    Only lines that become blank AS A RESULT of image/comment removal are dropped.

    Never modifies content inside fenced code blocks.
    """
    lines_out: list[str] = []
    in_comment = False

    for line, in_fence, is_delimiter in _iter_lines_fence_aware(markdown):
        if in_fence or is_delimiter:
            lines_out.append(line)
            continue

        content = line.rstrip("\n\r")
        eol = line[len(content) :]

        # Pre-existing blank lines (paragraph separators) pass through
        # untouched — unless we're inside an HTML comment, where a blank line
        # is comment content and must be dropped with the rest of it.
        if content.strip() == "" and not in_comment:
            lines_out.append(line)
            continue

        # Multi-line HTML comment tracking.
        if in_comment:
            close = _HTML_COMMENT_CLOSE_RE.search(content)
            if close:
                in_comment = False
                content = content[close.end() :]
            else:
                continue  # Entire line inside an HTML comment — drop it.

        # Strip single-line or self-contained <!-- ... --> comments.
        content = _HTML_COMMENT_INLINE_RE.sub("", content)

        # Check if an unclosed <!-- opens on this line.
        open_match = _HTML_COMMENT_OPEN_RE.search(content)
        if open_match:
            in_comment = True
            content = content[: open_match.start()]

        # Remove badge-style link-images [![...](...)](/...) first (superset of inline image).
        content = _BADGE_LINK_IMAGE_RE.sub("", content)
        # Remove remaining inline images ![...](...).
        content = _IMAGE_INLINE_RE.sub("", content)

        # Drop the line only if it became blank as a result of image/comment removal above.
        if content.strip() == "":
            continue

        lines_out.append(content + eol)

    return "".join(lines_out)


# Table-structure lines must never be treated as boilerplate.
# These include header/separator rows like: | Name | Type | or |---|---|
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|*-]+\|[\s:|*-]*$")


def _is_table_structure(text: str) -> bool:
    """Return True if *text* is a markdown table delimiter (header or separator row).

    We exclude these from boilerplate detection because identical column headers
    (e.g. ``| Name | Type | Required | Description |``) legitimately repeat on
    every endpoint page and must not be removed.
    """
    stripped = text.strip()
    # Any line starting with '|' is a table row.
    if stripped.startswith("|"):
        return True
    # Separator-only pattern: e.g. |---|---| with optional leading/trailing pipe.
    if _TABLE_SEPARATOR_RE.match(stripped):
        return True
    return False


def _find_boilerplate_lines(pages: list[Page]) -> set[str]:
    """Return lines appearing outside fences in > 30% of pages and >= 5 pages.

    Table-structure lines (header rows, separator rows) are never included even
    if they appear on every page — removing them would corrupt all tables.

    Only meaningful when len(pages) >= _BOILERPLATE_MIN_BUNDLE (returns empty set
    for smaller bundles to avoid removing legitimate content).
    """
    if len(pages) < _BOILERPLATE_MIN_BUNDLE:
        return set()

    line_page_counts: Counter = Counter()

    for page in pages:
        seen_in_page: set[str] = set()
        for line, in_fence, is_delimiter in _iter_lines_fence_aware(page.markdown):
            if in_fence or is_delimiter:
                continue
            text = line.strip()
            if not text:
                continue
            # Skip table-structure lines — they must never be flagged as boilerplate.
            if _is_table_structure(text):
                continue
            if text not in seen_in_page:
                line_page_counts[text] += 1
                seen_in_page.add(text)

    threshold_fraction = _BOILERPLATE_FRACTION * len(pages)
    return {
        line
        for line, count in line_page_counts.items()
        if count > threshold_fraction and count >= _BOILERPLATE_MIN_PAGES
    }


def _dedupe_boilerplate(pages: list[Page]) -> list[Page]:
    """Remove boilerplate lines from every page outside fenced blocks.

    Returns new Page objects; originals are not mutated.
    A line is boilerplate if its stripped text appears in > 30% of pages AND >= 5 pages.
    When len(pages) < 8 the pass is skipped (returns pages unchanged).
    """
    boilerplate = _find_boilerplate_lines(pages)
    if not boilerplate:
        return pages

    result: list[Page] = []
    for page in pages:
        lines_out: list[str] = []
        for line, in_fence, is_delimiter in _iter_lines_fence_aware(page.markdown):
            if in_fence or is_delimiter:
                lines_out.append(line)
                continue
            if line.strip() in boilerplate:
                continue
            lines_out.append(line)
        new_md = "".join(lines_out)
        if new_md == page.markdown:
            result.append(page)
        else:
            new_page = page.model_copy(
                update={"markdown": new_md, "word_count": len(new_md.split())}
            )
            result.append(new_page)

    return result


def _cap_page_words(markdown: str, max_words: int) -> str:
    """Truncate *markdown* at *max_words* on a line boundary; append a marker.

    Include complete lines until the running word count first reaches or exceeds
    *max_words*, then stop.  The first line that tips the count over the cap is
    still included (minimum truncation unit is one line); remaining lines are
    dropped and a marker is appended.
    """
    if len(markdown.split()) <= max_words:
        return markdown

    words_seen = 0
    lines_out: list[str] = []
    for line in markdown.splitlines(keepends=True):
        lines_out.append(line)
        words_seen += len(line.split())
        if words_seen >= max_words:
            break

    return "".join(lines_out) + "\n\n[content truncated — page exceeds word cap]"


# ── Public API ─────────────────────────────────────────────────────────────────


def _shrink_level() -> int:
    """Read TECHDOC_SHRINK_LEVEL from env; warn and default to 1 on invalid values."""
    raw = os.environ.get("TECHDOC_SHRINK_LEVEL", "1")
    try:
        level = int(raw)
        if level not in (0, 1, 2):
            raise ValueError()
        return level
    except ValueError:
        log.warning("shrink: invalid TECHDOC_SHRINK_LEVEL=%r — defaulting to 1", raw)
        return 1


def shrink_pages(pages: list[Page], level: int) -> tuple[list[Page], dict]:
    """Apply a deterministic shrink pass to *pages* and return new Page objects plus stats.

    Args:
        pages: The pages to shrink. Originals are never mutated.
        level: Shrink intensity.
            0 — identity (no changes).
            1 — safe: strip images/comments, remove boilerplate, cap page words.
            2 — level 1 + collapse giant JSON blocks + collapse equivalent code samples.

    Returns:
        A tuple (shrunk_pages, stats) where stats is a dict with keys:
            level, pages, words_before, words_after,
            passes: {pass_name: words_removed, ...}
    """
    if level not in (0, 1, 2):
        log.warning("shrink: invalid level=%r — defaulting to 1", level)
        level = 1

    words_before = sum(p.word_count for p in pages)
    passes: dict[str, int] = {}

    if level == 0:
        return list(pages), {
            "level": 0,
            "pages": len(pages),
            "words_before": words_before,
            "words_after": words_before,
            "passes": {},
        }

    # --- Level 1: safe passes ---

    current = pages
    after_strip = _apply_per_page(current, _strip_images)
    passes["images"] = _word_delta(current, after_strip)
    current = after_strip

    after_boilerplate = _dedupe_boilerplate(current)
    passes["boilerplate"] = _word_delta(current, after_boilerplate)
    current = after_boilerplate

    max_page_words = int(
        os.environ.get("TECHDOC_MAX_PAGE_WORDS", str(_DEFAULT_MAX_PAGE_WORDS))
    )
    after_cap = _apply_per_page(current, lambda md: _cap_page_words(md, max_page_words))
    # Note: page_cap stat is approximate — the truncation marker words are counted as saved.
    passes["page_cap"] = _word_delta(current, after_cap)
    current = after_cap

    if level == 2:
        # Pass 4: collapse oversized JSON schema blocks.
        after_json = _apply_per_page(current, _collapse_giant_json)
        passes["json_collapse"] = _word_delta(current, after_json)
        current = after_json

        # Pass 5: collapse equivalent multi-language code samples.
        after_samples = _apply_per_page(current, _collapse_code_samples)
        passes["code_samples"] = _word_delta(current, after_samples)
        current = after_samples

    words_after = sum(p.word_count for p in current)

    return current, {
        "level": level,
        "pages": len(current),
        "words_before": words_before,
        "words_after": words_after,
        "passes": passes,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


def _apply_per_page(pages: list[Page], fn) -> list[Page]:
    """Apply *fn(markdown) -> markdown* to each page; return new Page objects."""
    result: list[Page] = []
    for page in pages:
        new_md = fn(page.markdown)
        if new_md == page.markdown:
            result.append(page)
        else:
            new_page = page.model_copy(
                update={"markdown": new_md, "word_count": len(new_md.split())}
            )
            result.append(new_page)
    return result


def _word_delta(before: list[Page], after: list[Page]) -> int:
    """Return total words removed (positive = fewer words)."""
    return sum(p.word_count for p in before) - sum(p.word_count for p in after)
