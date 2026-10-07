"""Shared models. Keep this file small and stable — everything imports it."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Platform(StrEnum):
    """Detected documentation platform."""

    DOCUSAURUS = "docusaurus"
    MINTLIFY = "mintlify"
    README_IO = "readme_io"
    STOPLIGHT = "stoplight"
    REDOC = "redoc"
    SWAGGER_UI = "swagger_ui"
    GITBOOK = "gitbook"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class Fingerprint(BaseModel):
    """Result of detect.fingerprint(url)."""

    url: str
    platform: Platform
    confidence: float = Field(ge=0.0, le=1.0)
    signals: dict[str, Any] = Field(default_factory=dict)
    # Hints downstream strategies can use without re-fetching.
    raw_html_snippet: str | None = None
    openapi_url_hint: str | None = None
    sitemap_url: str | None = None


class Page(BaseModel):
    """A single extracted page of documentation."""

    url: str
    title: str
    markdown: str
    source_strategy: str
    word_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Bundle(BaseModel):
    """The full extraction result for one site."""

    name: str
    entry_url: str
    platform: Platform
    strategy: str
    pages: list[Page] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    errors: list[str] = Field(default_factory=list)

    @property
    def total_words(self) -> int:
        return sum(p.word_count for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)
