"""Explicit provider routing: never infer a provider from a model's name."""

import os

import httpx

from .config import PROVIDER_DEFAULTS
from .core.adapters.anthropic import AnthropicAdapter
from .core.adapters.openai import OpenAIAdapter

PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "custom": "the custom provider",
}


def key_name(provider):
    """The environment variable, and the Vault item, that holds a provider's key."""
    return f"{provider.upper()}_API_KEY"


def provider_key(provider, vault=None):
    """The provider's key: the environment's, else the Vault item of the
    same name saved from the Settings screen; "" when neither has one."""
    key = os.getenv(key_name(provider))
    if key:
        return key
    if vault is None:
        return ""
    try:
        item = vault.get_optional(key_name(provider))
    except ValueError:
        return ""
    value = (item or {}).get("value")
    return value.strip() if isinstance(value, str) else ""


def key_source(provider, vault=None):
    """Where the provider's key comes from: "environment", "vault" or ""."""
    if os.getenv(key_name(provider)):
        return "environment"
    return "vault" if provider_key(provider, vault) else ""


def provider_status(config, vault=None):
    """Whether executions can reach a model with *config*, and what is
    missing in words when they cannot."""
    missing = []
    if not str(config.get("model") or "").strip():
        missing.append("a model")
    provider = config.get("provider", "")
    if provider == "custom" and not config.get("provider_endpoint"):
        missing.append("the custom provider's endpoint")
    if provider and not provider_key(provider, vault):
        missing.append(f"the {PROVIDER_NAMES.get(provider, provider)} key")
    return {"configured": not missing, "missing": missing}


def list_models(provider, endpoint, wire, key, client=None):
    """The models the provider lists for the key: the key's test, and the
    list the Settings screen offers. ValueError says what went wrong."""
    endpoint = (endpoint or "").rstrip("/")
    if wire == "anthropic":
        url, headers = endpoint + "/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        url, headers = endpoint + "/models", ({"Authorization": f"Bearer {key}"} if key else {})
    name = PROVIDER_NAMES.get(provider, provider)
    try:
        with client or httpx.Client(timeout=20) as c:
            response = c.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ValueError(f"{name} could not be reached at {endpoint}: {exc}") from exc
    if response.status_code in (401, 403):
        raise ValueError(f"{name} refused the key")
    if response.status_code != 200:
        raise ValueError(f"{name} answered {response.status_code} to the models request")
    try:
        data = response.json().get("data") or []
    except ValueError as exc:
        raise ValueError(f"{name} answered something other than a models list") from exc
    ids = sorted({m.get("id") for m in data if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]})
    if not ids:
        raise ValueError(f"The key works, but {name} lists no models for it")
    return ids


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
    key = provider_key(provider, vault)
    if not key:
        raise ValueError(
            f"Set the {PROVIDER_NAMES.get(provider, provider)} key in Settings, "
            f"or {key_name(provider)} in the environment"
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
