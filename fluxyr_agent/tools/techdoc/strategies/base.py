"""Strategy ABC.

Each strategy:
  - inspects a Fingerprint and decides if it can handle the site.
  - returns a Bundle of pages on success, or None on miss.
  - never raises for "site doesn't match me" — that's a normal return.
  - may raise for unexpected errors; the pipeline logs them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Bundle, Fingerprint


class Strategy(ABC):
    name: str  # short identifier, e.g. "openapi"

    @abstractmethod
    def can_handle(self, fp: Fingerprint) -> bool:
        """Quick check: should the pipeline even try this strategy?

        This should be cheap — no network calls. Use it to skip strategies
        that have no chance based on the fingerprint signals.
        """

    @abstractmethod
    def extract(self, fp: Fingerprint, name: str) -> Bundle | None:
        """Do the actual work. Return a Bundle or None if the strategy
        ended up unable to extract useful content (e.g. found the spec
        was empty)."""
