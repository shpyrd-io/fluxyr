"""Page ranking and selection for distill(): which pages make the cut.

Split out of distill.py to keep that module inside the project's file-size
budget (see .claude/guidelines/shared/code-quality.md, "Limit Source Files to
200-300 Lines") — scoring/selection is a self-contained concern, independent
of the LLM-calling and chunking logic that stays in distill.py.
"""

from __future__ import annotations

import logging
import re

from .models import Bundle, Page

log = logging.getLogger(__name__)

# Default words-per-chunk for Anthropic — only consumed here, as select_pages'
# legacy default budget (distill() always passes an explicit budget_words).
_MAX_CHUNK_WORDS = 40_000

# Keywords in page title / URL → structural importance score.
_STRUCTURAL: dict[str, int] = {
    "auth": 10,
    "authentication": 10,
    "authorization": 10,
    "oauth": 9,
    "credential": 9,
    "credentials": 9,
    "api-key": 9,
    "apikey": 9,
    "api-keys": 9,
    "token": 7,
    "tokens": 7,
    "getting-started": 8,
    "quickstart": 8,
    "quick-start": 8,
    "introduction": 6,
    "overview": 5,
    "error": 9,
    "errors": 9,
    "error-codes": 9,
    "rate-limit": 8,
    "rate-limiting": 8,
    "ratelimit": 8,
    "throttl": 8,
    "pagination": 7,
    "paging": 6,
    "webhook": 6,
    "webhooks": 6,
    "security": 5,
    "header": 5,
    "headers": 5,
    # mTLS / certificate auth
    "mtls": 10,
    "mutual-tls": 10,
    "mutual_tls": 10,
    "certificate": 9,
    "certificates": 9,
    "client-cert": 9,
    "client-certificate": 9,
    "client-certificates": 9,
    "x509": 8,
    "x.509": 8,
    "ca-bundle": 8,
    "ca_bundle": 8,
    "pkcs": 7,
    "pkcs12": 7,
    "tls": 7,
    "ssl": 6,
    "pem": 6,
    "cert": 6,
}

_API_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD)\b"
    r"|/v\d+/"
    r"|\b(endpoint|request|response|authorization|bearer|api.?key|webhook|oauth|token)\b"
    r"|\b(mtls|mutual.tls|client.cert|certificate|x509|pkcs12|ca.bundle|ssl.context)\b"
    r"|--cert|--key\b|--cacert"
    r"|```\s*(json|yaml|curl|http)",
    re.IGNORECASE,
)


def _score_page(page: Page) -> float:
    combined = f"{page.title} {page.url}".lower()
    structural = max(
        (_STRUCTURAL[kw] for kw in _STRUCTURAL if kw in combined),
        default=0,
    )
    hits = len(_API_RE.findall(page.markdown))
    density = hits / max(1, page.word_count) * 1_000
    return structural * 2 + density


def select_pages(bundle: Bundle, budget_words: int | None = None) -> list[Page]:
    """Return the subset of bundle pages most useful for integration.

    Pages are ranked by structural importance (auth, errors, overview) and API
    signal density.  Selection stops when the running word total reaches
    *budget_words*.
    """
    scored = sorted(bundle.pages, key=_score_page, reverse=True)
    selected: list[Page] = []
    total_words = 0
    if budget_words is None:
        budget_words = (
            _MAX_CHUNK_WORDS * 8
        )  # legacy default; distill() always passes budget
    budget = budget_words

    for page in scored:
        if total_words >= budget:
            break
        selected.append(page)
        total_words += page.word_count

    log.info(
        "distill: selected %d/%d pages (%d words)",
        len(selected),
        len(bundle.pages),
        total_words,
    )
    return selected
