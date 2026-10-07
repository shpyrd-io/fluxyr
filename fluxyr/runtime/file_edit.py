"""Targeted replacements against one original file, with conservative matching."""

import difflib
import re
import unicodedata
from itertools import pairwise


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(
        str.maketrans(
            {
                **{c: "'" for c in "\u2018\u2019\u201a\u201b"},
                **{c: '"' for c in "\u201c\u201d\u201e\u201f"},
                **{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"},
            }
        )
    )
    return "\n".join(line.rstrip() for line in text.split("\n"))


def lf(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def replace_blocks(raw, edits, path):
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must contain at least one replacement")
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    original = lf(raw.removeprefix(bom)) if bom else lf(raw)
    ending = "\r\n" if "\r\n" in raw else "\n"
    prepared = []
    for edit in edits:
        if not isinstance(edit, dict) or not all(
            isinstance(edit.get(k), str) for k in ("oldText", "newText")
        ):
            raise ValueError("Each edit requires string oldText and newText")
        old, new = lf(edit["oldText"]), lf(edit["newText"])
        if not old:
            raise ValueError("oldText must not be empty")
        prepared.append((old, new))
    fuzzy = any(old not in original for old, _ in prepared)
    base = normalize(original) if fuzzy else original
    matches = []
    for i, (old, new) in enumerate(prepared):
        needle = normalize(old) if fuzzy else old
        if not needle:
            raise ValueError(f"edits[{i}].oldText must not normalize to empty text")
        positions = [
            m.start() for m in re.finditer("(?=" + re.escape(needle) + ")", base)
        ]
        if not positions:
            raise ValueError(
                f"Could not find edits[{i}] in {path}; read the file again"
            )
        if len(positions) != 1:
            raise ValueError(
                f"Found {len(positions)} occurrences of edits[{i}]; provide unique surrounding text"
            )
        matches.append((positions[0], positions[0] + len(needle), new, i))
    matches.sort()
    for prev, current in pairwise(matches):
        if prev[1] > current[0]:
            raise ValueError(
                f"edits[{prev[3]}] and edits[{current[3]}] overlap; merge them"
            )
    if fuzzy:
        # Copy unaffected original lines byte-for-byte, normalize only touched lines.
        lines = base.splitlines(keepends=True)
        originals = original.splitlines(keepends=True)
        if len(lines) != len(originals):
            raise ValueError("Normalization changed line boundaries; use exact oldText")
        offsets, cursor = [], 0
        for line in lines:
            offsets.append((cursor, cursor + len(line)))
            cursor += len(line)
        groups = []
        for match in matches:
            start, end = match[:2]
            first = next(i for i, (a, b) in enumerate(offsets) if a <= start < b)
            last = next(i for i, (a, b) in enumerate(offsets) if a < end <= b) + 1
            if groups and first < groups[-1][1]:
                groups[-1][1] = max(last, groups[-1][1])
                groups[-1][2].append(match)
            else:
                groups.append([first, last, [match]])
        pieces, cursor = [], 0
        for first, last, changes in groups:
            pieces.extend(originals[cursor:first])
            offset = offsets[first][0]
            block = "".join(lines[first:last])
            for start, end, new, _ in reversed(changes):
                block = block[: start - offset] + new + block[end - offset :]
            pieces.append(block)
            cursor = last
        pieces.extend(originals[cursor:])
        changed = "".join(pieces)
    else:
        changed = base
        for start, end, new, _ in reversed(matches):
            changed = changed[:start] + new + changed[end:]
    if changed == original:
        raise ValueError("No changes made; replacements produced identical content")
    patch = "".join(
        difflib.unified_diff(
            original.splitlines(True),
            changed.splitlines(True),
            fromfile=path,
            tofile=path,
            n=4,
        )
    )
    line = original[: matches[0][0]].count("\n") + 1
    return bom + changed.replace("\n", ending), patch, line
