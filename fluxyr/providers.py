"""Explicit provider routing: never infer a provider from a model's name."""

import os

from .config import PROVIDER_DEFAULTS
from .core.adapters.anthropic import AnthropicAdapter
from .core.adapters.openai import OpenAIAdapter


def make_adapter(config, vault=None):
    if not str(config.get("model") or "").strip():
        raise ValueError("FLUXYR_MODEL is required to start the agent worker")
    provider = config["provider"]
    if provider not in (*PROVIDER_DEFAULTS, "custom"):
        raise ValueError("Unsupported model provider")
    wire, endpoint = PROVIDER_DEFAULTS.get(provider, ("", ""))
    wire = config.get("provider_format") or wire
    endpoint = config.get("provider_endpoint") or endpoint
    if wire not in ("openai", "anthropic") or not endpoint:
        raise ValueError(
            "Custom provider requires a provider_endpoint and openai or anthropic provider_format"
        )
    key = os.getenv(f"{provider.upper()}_API_KEY")
    if not key:
        raise ValueError(
            f"Set {provider.upper()}_API_KEY in the environment and restart"
        )
    if wire == "anthropic":
        return AnthropicAdapter(
            model=config["model"],
            api_key=key,
            base_url=endpoint,
            provider=provider,
            max_tokens=int(config.get("max_tokens", 16000)),
            thinking_mode=config.get("thinking_mode", "none"),
            thinking_budget=int(config.get("thinking_budget", 0)),
        )
    reasoning = (
        {"effort": config["reasoning_effort"]}
        if config.get("reasoning_effort")
        else None
    )
    if provider == "openrouter" and reasoning is None:
        if config.get("thinking_mode") == "adaptive":
            reasoning = {"enabled": True}
        elif config.get("thinking_mode") == "enabled":
            reasoning = {"max_tokens": int(config["thinking_budget"])}
    return OpenAIAdapter(
        model=config["model"],
        api_key=key,
        provider=provider,
        base_url=endpoint,
        max_tokens=int(config.get("max_tokens", 16000)),
        reasoning=reasoning,
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
