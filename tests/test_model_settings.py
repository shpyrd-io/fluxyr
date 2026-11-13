"""Model settings from the Settings screen: stored in the database, the key in
the Vault, the environment's values fixed."""

import json

import httpx
import pytest

from fluxyr.app import create_app
from fluxyr.config import Settings
from fluxyr.providers import list_models, make_adapter, provider_status


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "FLUXYR_PROVIDER",
        "FLUXYR_PROVIDER_FORMAT",
        "FLUXYR_PROVIDER_ENDPOINT",
        "FLUXYR_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "CUSTOM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def instance(tmp_path, database_url):
    app = create_app(
        Settings(database_url=database_url, root=tmp_path / "instance", testing=True)
    )
    engine = app.extensions["engine"]
    yield app, engine, app.test_client()
    engine.stop()
    engine.db.engine.dispose()


def test_stored_settings_fill_what_the_environment_leaves_open(instance):
    _, engine, _ = instance
    assert engine.db.model_config()["model"] == ""
    engine.db.configuration_set("model", {"provider": "anthropic", "model": "claude-x"})
    config = engine.db.model_config()
    assert (config["provider"], config["model"]) == ("anthropic", "claude-x")
    # The endpoint and format follow the stored provider.
    assert config["provider_endpoint"] == "https://api.anthropic.com"
    assert config["provider_format"] == "anthropic"


def test_the_environment_wins_over_stored_settings(instance, monkeypatch):
    _, engine, _ = instance
    engine.db.configuration_set("model", {"provider": "anthropic", "model": "claude-x"})
    monkeypatch.setenv("FLUXYR_PROVIDER", "openrouter")
    config = engine.db.model_config()
    assert config["provider"] == "openrouter"
    assert config["model"] == "claude-x"
    assert config["provider_endpoint"] == "https://openrouter.ai/api/v1"


def test_the_key_comes_from_the_vault_when_the_environment_has_none(instance):
    _, engine, _ = instance
    config = {**engine.db.model_config(), "provider": "openrouter", "model": "m"}
    with pytest.raises(ValueError, match="Set the OpenRouter key in Settings"):
        make_adapter(config, engine.vault)
    assert provider_status(config, engine.vault) == {
        "configured": False,
        "missing": ["the OpenRouter key"],
    }
    engine.vault.put("OPENROUTER_API_KEY", "text", {"value": "sk-or-vault"})
    adapter = make_adapter(config, engine.vault)
    assert adapter.client.api_key == "sk-or-vault"
    assert provider_status(config, engine.vault)["configured"] is True


def models_transport(key, ids):
    def handle(request):
        if request.headers.get("Authorization") != f"Bearer {key}" and request.headers.get("x-api-key") != key:
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    return httpx.MockTransport(handle)


def test_the_models_list_is_the_keys_test():
    client = httpx.Client(transport=models_transport("good", ["b/two", "a/one"]))
    assert list_models("openrouter", "https://openrouter.ai/api/v1", "openai", "good", client) == ["a/one", "b/two"]
    client = httpx.Client(transport=models_transport("good", ["b/two"]))
    with pytest.raises(ValueError, match="OpenRouter refused the key"):
        list_models("openrouter", "https://openrouter.ai/api/v1", "openai", "bad", client)
    client = httpx.Client(transport=models_transport("good", []))
    with pytest.raises(ValueError, match="lists no models"):
        list_models("openrouter", "https://openrouter.ai/api/v1", "openai", "good", client)


def test_settings_are_saved_through_the_api_with_the_key_in_the_vault(instance, monkeypatch):
    _, engine, client = instance
    view = client.get("/api/settings").get_json()
    assert view["configured"] is False
    assert "a model" in view["missing"]
    assert view["managed"]["model"] == "settings"

    saved = client.post(
        "/api/settings",
        json={"provider": "openrouter", "model": "minimax/m3", "api_key": "sk-or-1"},
    ).get_json()
    assert saved["configured"] is True
    assert saved["model"] == "minimax/m3"
    assert saved["key_source"] == "vault"
    assert saved["configured_keys"]["openrouter"] is True
    assert "sk-or-1" not in json.dumps(saved)
    assert engine.vault.get_optional("OPENROUTER_API_KEY") == {"value": "sk-or-1"}
    assert engine.db.model_config()["model"] == "minimax/m3"

    # A later save without a key keeps the one in the Vault.
    again = client.post("/api/settings", json={"provider": "openrouter", "model": "other/m"}).get_json()
    assert again["configured"] is True and again["model"] == "other/m"

    # The environment's values cannot be changed from here.
    monkeypatch.setenv("FLUXYR_MODEL", "env/model")
    refused = client.post("/api/settings", json={"provider": "openrouter", "model": "x"})
    assert refused.status_code == 400
    assert "FLUXYR_MODEL is set in the environment" in refused.get_json()["error"]
    assert client.get("/api/settings").get_json()["managed"]["model"] == "environment"


def test_a_custom_provider_needs_its_endpoint_and_format(instance):
    _, _, client = instance
    refused = client.post("/api/settings", json={"provider": "custom", "model": "m", "provider_format": "openai"})
    assert "needs its endpoint" in refused.get_json()["error"]
    refused = client.post(
        "/api/settings",
        json={"provider": "custom", "model": "m", "provider_format": "openai", "provider_endpoint": "ftp://x"},
    )
    assert "HTTP(S)" in refused.get_json()["error"]
    saved = client.post(
        "/api/settings",
        json={"provider": "custom", "model": "m", "provider_format": "openai", "provider_endpoint": "http://llm.local/v1"},
    ).get_json()
    assert saved["provider_endpoint"] == "http://llm.local/v1"
    assert saved["configured"] is False  # no key for a custom provider yet
