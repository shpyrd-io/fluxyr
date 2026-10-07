"""Shared HTTP client. Be polite (User-Agent), retry transient errors,
keep timeouts reasonable. Used by every strategy that doesn't need a
real browser."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "techdoc-poc/0.1 (+https://github.com/iugu/techdoc-poc; "
    "Markdown extraction for LLMs)"
)

_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def make_client(**overrides: Any) -> httpx.Client:
    """Create a configured httpx.Client. Reuse one per pipeline run."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=_DEFAULT_TIMEOUT,
        follow_redirects=True,
        **overrides,
    )


def get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 1.5,
) -> httpx.Response | None:
    """GET with exponential backoff on 5xx and network errors.
    Returns None instead of raising — callers decide what to do with absence."""
    for attempt in range(retries + 1):
        try:
            r = client.get(url)
        except (httpx.RequestError, httpx.TimeoutException) as e:
            log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt == retries:
                return None
            time.sleep(backoff**attempt)
            continue

        if r.status_code < 500:
            return r

        log.warning("GET %s -> %d (attempt %d)", url, r.status_code, attempt + 1)
        if attempt == retries:
            return r
        time.sleep(backoff**attempt)

    return None
