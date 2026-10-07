"""Strategy registry.

Strategies are tried in this order; first one returning a Bundle wins."""

from __future__ import annotations

from .base import Strategy
from .docusaurus import DocusaurusStrategy
from .generic_crawler import GenericCrawlerStrategy
from .llms_txt import LlmsTxtStrategy
from .openapi import OpenApiStrategy
from .readme_io import ReadMeIoStrategy

# Order matters. Cheap → expensive.
STRATEGIES: list[Strategy] = [
    LlmsTxtStrategy(),
    OpenApiStrategy(),
    ReadMeIoStrategy(),
    DocusaurusStrategy(),
    GenericCrawlerStrategy(),
]

STRATEGIES_BY_NAME: dict[str, Strategy] = {s.name: s for s in STRATEGIES}
