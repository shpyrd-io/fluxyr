"""TOTP provisioning and generation. These functions are private runtime APIs."""

import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, unquote, urlsplit


def configuration(content):
    """Normalize a Base32 seed or otpauth URI without leaking parser input."""
    try:
        values = dict(content)
        seed = values.get("secret", "").strip()
        if seed.startswith("otpauth://"):
            uri = urlsplit(seed)
            if uri.netloc != "totp":
                raise ValueError()
            query = parse_qs(uri.query, strict_parsing=True)
            if any(len(v) != 1 for v in query.values()):
                raise ValueError()
            seed = query["secret"][0]
            for key in ("algorithm", "digits", "period", "issuer"):
                if key in query:
                    values[key] = query[key][0]
            values["account"] = unquote(uri.path.lstrip("/"))
        seed = "".join(seed.split()).upper().rstrip("=")
        raw = base64.b32decode(seed + "=" * (-len(seed) % 8))
        algorithm = str(values.get("algorithm") or "SHA1").upper()
        digits = int(values.get("digits") or 6)
        period = int(values.get("period") or 30)
        if not raw or algorithm not in ("SHA1", "SHA256", "SHA512"):
            raise ValueError()
        if digits not in (6, 8) or not 15 <= period <= 120:
            raise ValueError()
        return {
            **values,
            "secret": seed,
            "algorithm": algorithm,
            "digits": digits,
            "period": period,
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError(
            "Invalid TOTP settings. Use a Base32 secret or a totp otpauth URI, SHA1/SHA256/SHA512, 6/8 digits and a 15–120 second period."
        ) from None


def generate(content, now=None):
    config = configuration(content)
    now = time.time() if now is None else now
    seed = config["secret"]
    key = base64.b32decode(seed + "=" * (-len(seed) % 8))
    counter = int(now // config["period"])
    digest = hmac.new(
        key, struct.pack(">Q", counter), getattr(hashlib, config["algorithm"].lower())
    ).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** config["digits"])).zfill(config["digits"]), (
        counter + 1
    ) * config["period"]
