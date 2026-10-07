"""Explicit provider routing: never infer a provider from a model's name."""

import os

from .core.adapters.anthropic import AnthropicAdapter
from .core.adapters.openai import OpenAIAdapter


def make_adapter(config, vault=None):
    provider = config["provider"]
    if provider not in ("anthropic", "openai", "openrouter"):
        raise ValueError("Unsupported model provider")
    key = os.getenv(f"{provider.upper()}_API_KEY")
    if vault:
        stored = vault.get_optional(f"provider:{provider}")
        key = (stored or {}).get("value") or key
    if not key:
        raise ValueError(f"Configure the {provider} API key in Settings")
    if provider == "anthropic":
        return AnthropicAdapter(
            model=config["model"],
            api_key=key,
            max_tokens=int(config.get("max_tokens", 16000)),
            thinking_mode=config.get("thinking_mode", "none"),
            thinking_budget=int(config.get("thinking_budget", 0)),
        )
    return OpenAIAdapter(
        model=config["model"],
        api_key=key,
        provider=provider,
        base_url=config.get("base_url")
        or ("https://openrouter.ai/api/v1" if provider == "openrouter" else None),
        max_tokens=int(config.get("max_tokens", 16000)),
        reasoning={"effort": config["reasoning_effort"]}
        if config.get("reasoning_effort")
        else None,
    )


def response_text(response):
    if not response:
        return ""
    content = response.get("content", "")
    if isinstance(content, str):
        return content
    return "\n".join(
        b.get("text", "") for b in content or [] if b.get("type") == "text"
    )
