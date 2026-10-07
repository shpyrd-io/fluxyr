"""Environment-backed instance configuration (explicit overrides support tests)."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PROVIDER_DEFAULTS = {
    "anthropic": ("anthropic", "https://api.anthropic.com"),
    "openai": ("openai", "https://api.openai.com/v1"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1"),
}


def env(name, default, cast=str, *, prefer=None):
    def read():
        source = prefer if prefer and os.getenv(prefer) else name
        value = os.getenv(source) or default
        try:
            return cast(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{source} has an invalid value") from exc

    return field(default_factory=read)


@dataclass
class Settings:
    agent_name: str = env("FLUXYR_AGENT_NAME", "Default Agent")
    database_url: str = env("DATABASE_URL", "")
    root: Path = env("FLUXYR_ROOT", ".", Path)  # noqa: RUF009 - env returns a dataclass field factory
    skills_dir: str = env("FLUXYR_SKILLS_DIR", "")
    workers: int = env("FLUXYR_WORKERS", "4", int)
    tool_workers: int = env("FLUXYR_TOOL_WORKERS", "6", int)
    lease_seconds: int = env("FLUXYR_LEASE_SECONDS", "90", int)
    tool_timeout: int = env("FLUXYR_TOOL_TIMEOUT", "300", int)
    bash_max_output_bytes: int = env("FLUXYR_BASH_MAX_OUTPUT_BYTES", "33554432", int)
    execution_mode: str = env("FLUXYR_EXECUTION_MODE", "local")
    workspace_retention_days: int = env("FLUXYR_WORKSPACE_RETENTION_DAYS", "30", int)
    max_iterations: int = env("FLUXYR_MAX_ITERATIONS", "40", int)
    host: str = env("FLUXYR_HOST", "127.0.0.1")
    port: int = env("PORT", "5050", int)
    http_threads: int = env("FLUXYR_HTTP_THREADS", "24", int)
    max_content_length: int = env("FLUXYR_MAX_CONTENT_LENGTH", "4194304", int)
    provider: str = env("FLUXYR_PROVIDER", "openrouter")
    model: str = env("FLUXYR_MODEL", "")
    provider_endpoint: str = env("FLUXYR_PROVIDER_ENDPOINT", "")
    provider_format: str = env("FLUXYR_PROVIDER_FORMAT", "")
    max_tokens: int = env("FLUXYR_MAX_TOKENS", "16000", int)
    reasoning_effort: str = env("FLUXYR_REASONING_EFFORT", "")
    thinking_mode: str = env("FLUXYR_THINKING_MODE", "none")
    thinking_budget: int = env("FLUXYR_THINKING_BUDGET", "0", int)
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
        if self.execution_mode not in ("local", "landlock"):
            raise ValueError("FLUXYR_EXECUTION_MODE must be local or landlock")
        self.root = self.root.resolve()
        if not self.database_url:
            from sqlalchemy.engine import URL

            self.database_url = URL.create(
                "sqlite", database=str(self.runtime / "fluxyr.sqlite3")
            ).render_as_string(hide_password=False)
        limits = {
            "workers": (1, 32),
            "tool_workers": (1, 32),
            "lease_seconds": (30, 86400),
            "tool_timeout": (1, 86400),
            "bash_max_output_bytes": (65536, 1073741824),
            "workspace_retention_days": (0, 36500),
            "max_iterations": (1, 1000),
            "port": (1, 65535),
            "http_threads": (2, 256),
            "max_content_length": (1024, 1073741824),
            "max_tokens": (256, 128000),
        }
        for name, (lo, hi) in limits.items():
            if not lo <= getattr(self, name) <= hi:
                variable = "PORT" if name == "port" else f"FLUXYR_{name.upper()}"
                raise ValueError(f"{variable} must be between {lo} and {hi}")
        if self.provider not in (*PROVIDER_DEFAULTS, "custom"):
            raise ValueError(
                "FLUXYR_PROVIDER must be anthropic, openai, openrouter or custom"
            )
        if self.provider == "custom":
            if self.provider_format not in ("openai", "anthropic"):
                raise ValueError(
                    "FLUXYR_PROVIDER_FORMAT must be openai or anthropic for custom"
                )
            if not self.provider_endpoint or not self.model:
                raise ValueError(
                    "Custom provider requires FLUXYR_PROVIDER_ENDPOINT and FLUXYR_MODEL"
                )
        elif (
            self.provider_format
            and self.provider_format != PROVIDER_DEFAULTS[self.provider][0]
        ):
            raise ValueError(
                "FLUXYR_PROVIDER_FORMAT conflicts with the selected provider; use custom"
            )
        endpoint = self.model_defaults()["provider_endpoint"]
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "FLUXYR_PROVIDER_ENDPOINT must be an HTTP(S) API base URL without query or fragment"
            )
        if self.thinking_mode not in ("none", "adaptive", "enabled"):
            raise ValueError("Invalid FLUXYR_THINKING_MODE")
        if (
            self.thinking_mode == "enabled"
            and not 1024 <= self.thinking_budget < self.max_tokens
        ):
            raise ValueError(
                "FLUXYR_THINKING_BUDGET must be >=1024 and below FLUXYR_MAX_TOKENS"
            )
        self.root = self.root.resolve()
        for path in (self.data, self.runtime, self.workspace):
            path.mkdir(parents=True, exist_ok=True)
        self.runtime.chmod(0o700)
        if self.execution_mode == "landlock":
            from .runtime.landlock import validate_support

            validate_support([self.data, self.workspace])

    def model_defaults(self):
        wire, endpoint = PROVIDER_DEFAULTS.get(self.provider, ("", ""))
        return {
            "provider": self.provider,
            "model": self.model,
            "provider_endpoint": self.provider_endpoint or endpoint,
            "provider_format": self.provider_format or wire,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "thinking_mode": self.thinking_mode,
            "thinking_budget": self.thinking_budget,
        }

    def validate_credentials(self):
        if not self.testing and not self.model.strip():
            raise ValueError("FLUXYR_MODEL is required to start the agent worker")
        name = self.provider.upper() + "_API_KEY"
        if not self.testing and not os.getenv(name):
            raise ValueError(f"{name} is required to start the agent worker")
