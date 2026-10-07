"""Repair escaped non-ASCII characters in model-authored prose only."""

import json
import re


def prose_unicode(value):
    if not isinstance(value, str):
        return value

    def decode(match):
        raw = match.group()
        decoded = json.loads('"' + raw + '"')
        if any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 128 for c in decoded):
            return raw
        return decoded

    return re.sub(
        r"(?<!\\)\\u[0-9a-fA-F]{4}(?:\\u[dD][c-fC-F][0-9a-fA-F]{2})?", decode, value
    )
