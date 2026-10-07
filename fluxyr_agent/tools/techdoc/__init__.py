"""Extract and distill developer documentation using the local engine adapter."""

from __future__ import annotations

__version__ = "0.1.0"

# ── Core models ──────────────────────────────────────────────────────────────
# ── Detection ────────────────────────────────────────────────────────────────
from .detect import fingerprint

# Distillation uses the instance adapter factory.
from .distill import distill, distill_to_file, get_integration_docs, select_pages
from .models import Bundle, Fingerprint, Page, Platform

# ── Pipeline ─────────────────────────────────────────────────────────────────
from .pipeline import run, write_bundle

# ── Strategy base class (for custom strategies) ───────────────────────────────
from .strategies.base import Strategy

__all__ = [
    # models
    "Bundle",
    "Fingerprint",
    "Page",
    "Platform",
    # pipeline
    "run",
    "write_bundle",
    # detection
    "fingerprint",
    # distillation
    "distill",
    "distill_to_file",
    "get_integration_docs",
    "select_pages",
    # extension
    "Strategy",
]
