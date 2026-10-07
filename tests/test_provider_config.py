"""Environment routing and real SDK serialization; HTTP responses are test fixtures."""

import json

import httpx
import pytest

from fluxyr.config import PROVIDER_DEFAULTS, Settings
from fluxyr.providers import make_adapter


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "PORT",
        "FLUXYR_PORT",
        "FLUXYR_PROVIDER",
        "FLUXYR_PROVIDER_FORMAT",
        "FLUXYR_PROVIDER_ENDPOINT",
        "FLUXYR_MODEL",
        "FLUXYR_AGENT_NAME",
        "FLUXYR_REASONING_EFFORT",
        "FLUXYR_THINKING_MODE",
        "FLUXYR_THINKING_BUDGET",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "CUSTOM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "port,fluxyr_port,expected",
    [
        (None, None, 5050),
        (None, "5059", 5050),
        ("8080", "5059", 8080),
        ("", "5059", 5050),
    ],
)
def test_port_uses_standard_environment_only(monkeypatch, port, fluxyr_port, expected):
    if port is not None:
        monkeypatch.setenv("PORT", port)
    if fluxyr_port is not None:
        monkeypatch.setenv("FLUXYR_PORT", fluxyr_port)
    assert Settings().port == expected


def test_invalid_port_is_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT", "invalid")
    with pytest.raises(ValueError, match="PORT has an invalid value"):
        Settings()
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValueError, match="PORT.*between"):
        Settings(database_url="sqlite://", root=tmp_path).prepare()


@pytest.mark.parametrize("provider", PROVIDER_DEFAULTS)
def test_built_in_defaults(provider, monkeypatch):
    # SDK environment must not accidentally reroute a built-in provider.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unwanted.invalid/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unwanted.invalid")
    monkeypatch.setenv(provider.upper() + "_API_KEY", "test-key")
    wire, endpoint = PROVIDER_DEFAULTS[provider]
    model = "chosen-model"
    config = Settings(provider=provider, model=model).model_defaults()
    assert config["model"] == model
    assert config["provider_format"] == wire
    adapter = make_adapter(config)
    try:
        assert str(adapter.client.base_url).rstrip("/") == endpoint
    finally:
        adapter.client.close()


@pytest.mark.parametrize("wire", ["openai", "anthropic"])
def test_custom_request_uses_selected_protocol_endpoint_and_credentials(
    wire, monkeypatch, tmp_path
):
    monkeypatch.setenv("CUSTOM_API_KEY", "custom-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-key")
    settings = Settings(
        database_url="sqlite://",
        root=tmp_path,
        provider="custom",
        model="custom-model",
        provider_format=wire,
        provider_endpoint="https://example.invalid/compatible",
        thinking_mode="adaptive" if wire == "anthropic" else "none",
        reasoning_effort="low" if wire == "openai" else "",
    )
    settings.prepare()
    requests = []

    def respond(request):
        requests.append(request)
        if wire == "anthropic":
            body = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "custom-model",
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }
        else:
            body = {
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "model": "custom-model",
                "created": 1,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            }
        return httpx.Response(200, json=body)

    adapter = make_adapter(settings.model_defaults())
    original = adapter.client
    adapter.client = original.with_options(
        http_client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    original.close()
    usage = []
    adapter._usage_callback = usage.append
    try:
        result, tools, _ = adapter.execute_step_with_usage(
            [{"role": "user", "content": "Hi"}], system_prompt="Be concise", tools=[]
        )
        assert result and not tools
        request = requests[0]
        body = json.loads(request.content)
        assert request.url.path == (
            "/compatible/v1/messages"
            if wire == "anthropic"
            else "/compatible/chat/completions"
        )
        assert body["model"] == "custom-model"
        assert body["max_tokens"] == 16000
        if wire == "anthropic":
            assert request.headers["x-api-key"] == "custom-test-key"
            assert body["thinking"] == {"type": "adaptive"}
        else:
            assert request.headers["authorization"] == "Bearer custom-test-key"
            assert body["reasoning_effort"] == "low"
            assert "reasoning" not in body and "usage" not in body
        assert usage[0]["provider"] == "custom"
    finally:
        adapter.client.close()


@pytest.mark.parametrize(
    "mode,effort,budget,expected",
    [
        ("none", "", 0, None),
        ("adaptive", "", 0, {"enabled": True}),
        ("enabled", "", 2048, {"max_tokens": 2048}),
        ("adaptive", "high", 0, {"effort": "high"}),
    ],
)
def test_openrouter_reasoning_mapping(monkeypatch, mode, effort, budget, expected):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings = Settings(
        provider="openrouter",
        model="test-model",
        thinking_mode=mode,
        thinking_budget=budget,
        reasoning_effort=effort,
    )
    adapter = make_adapter(settings.model_defaults())
    try:
        from fluxyr.core.adapters.openai_request_options import (
            build_create_kwargs,
        )

        kwargs = build_create_kwargs(
            model=settings.model_defaults()["model"],
            formatted_messages=[],
            formatted_tools=[],
            wire_tool_choice=None,
            stream=True,
            max_tokens=16000,
            reasoning=adapter._reasoning,
            provider_preferences=None,
            provider="openrouter",
        )
        assert kwargs["extra_body"].get("reasoning") == expected
    finally:
        adapter.client.close()


@pytest.mark.parametrize(
    "options,match",
    [
        ({"provider": "custom"}, "FLUXYR_PROVIDER_FORMAT"),
        (
            {"provider": "custom", "provider_format": "openai"},
            "FLUXYR_PROVIDER_ENDPOINT",
        ),
        ({"provider": "openai", "provider_format": "anthropic"}, "conflicts"),
        ({"provider_endpoint": "file:///tmp/api"}, "HTTP"),
    ],
)
def test_invalid_provider_configuration(tmp_path, options, match):
    with pytest.raises(ValueError, match=match):
        Settings(database_url="sqlite://", root=tmp_path, **options).prepare()


def test_custom_credentials_do_not_fall_back_to_another_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-key")
    settings = Settings(
        provider="custom",
        provider_format="openai",
        provider_endpoint="http://localhost:8000/v1",
        model="local",
    )
    with pytest.raises(ValueError, match="CUSTOM_API_KEY"):
        settings.validate_credentials()
    with pytest.raises(ValueError, match="CUSTOM_API_KEY"):
        make_adapter(settings.model_defaults())


def test_agent_name_public_settings(make_app, monkeypatch):
    assert Settings().agent_name == "Default Agent"
    monkeypatch.setenv("FLUXYR_AGENT_NAME", "My Custom Agent")
    app, _, _ = make_app()
    config = app.test_client().get("/api/settings").json
    assert config["agent_name"] == "My Custom Agent"
    assert "base_url" not in config


def test_m3_without_thinking_environment_uses_provider_defaults(monkeypatch):
    monkeypatch.setenv("FLUXYR_PROVIDER", "openrouter")
    monkeypatch.setenv("FLUXYR_MODEL", "minimax/minimax-m3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    adapter = make_adapter(Settings().model_defaults())
    try:
        assert adapter._reasoning is None
        from fluxyr.core.adapters.openai_request_options import (
            build_create_kwargs,
        )

        request = build_create_kwargs(
            model="minimax/minimax-m3",
            formatted_messages=[],
            formatted_tools=[],
            wire_tool_choice=None,
            stream=True,
            max_tokens=16000,
            reasoning=adapter._reasoning,
            provider_preferences=None,
            provider="openrouter",
        )
        assert "reasoning" not in request.get("extra_body", {})
        assert "thinking" not in request
        assert "reasoning_effort" not in request
    finally:
        adapter.client.close()
