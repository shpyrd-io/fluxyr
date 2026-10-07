"""Instance configuration. No tenant or authentication configuration exists."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://fluxyr:fluxyr@localhost:5432/fluxyr_agent",
        )
    )
    root: Path = field(
        default_factory=lambda: Path(os.getenv("FLUXYR_ROOT", ".")).resolve()
    )
    workers: int = field(default_factory=lambda: int(os.getenv("FLUXYR_WORKERS", "4")))
    tool_workers: int = field(
        default_factory=lambda: int(os.getenv("FLUXYR_TOOL_WORKERS", "6"))
    )
    lease_seconds: int = 90
    tool_timeout: int = 300
    max_iterations: int = 40
    host: str = field(default_factory=lambda: os.getenv("FLUXYR_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("FLUXYR_PORT", "5050")))
    testing: bool = False

    @property
    def data(self):
        return self.root / "data"

    @property
    def runtime(self):
        return self.root / ".runtime"

    @property
    def workspace(self):
        return self.root / "workspace"

    def prepare(self):
        self.root = self.root.resolve()
        for path in (self.data, self.runtime, self.workspace):
            path.mkdir(parents=True, exist_ok=True)
        self.runtime.chmod(0o700)
        if not 1 <= self.workers <= 32:
            raise ValueError("FLUXYR_WORKERS must be between 1 and 32")

    def model_defaults(self):
        return {
            "provider": os.getenv("FLUXYR_PROVIDER", "anthropic"),
            "model": os.getenv("FLUXYR_MODEL", "claude-sonnet-4-6"),
            "base_url": "",
            "max_tokens": 16000,
            "reasoning_effort": "",
            "thinking_mode": "none",
            "thinking_budget": 0,
        }
