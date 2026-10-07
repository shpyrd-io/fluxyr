import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import execute_next
from sqlalchemy import select

from fluxyr.core.adapters.openai_request_options import build_create_kwargs
from fluxyr.core.memory.short_term import ShortTermMemory


def test_openai_and_openrouter_wire_options():
    base = dict(
        model="any",
        formatted_messages=[],
        formatted_tools=None,
        wire_tool_choice=None,
        stream=True,
        max_tokens=5000,
        reasoning={"effort": "high"},
        provider_preferences=None,
    )
    direct = build_create_kwargs(**base, provider="openai")
    routed = build_create_kwargs(**base, provider="openrouter")
    assert (
        direct["max_completion_tokens"] == 5000 and direct["reasoning_effort"] == "high"
    )
    assert "extra_body" not in direct
    assert routed["max_tokens"] == 5000 and routed["extra_body"]["reasoning"] == {
        "effort": "high"
    }


def test_memory_pruning_keeps_tool_call_result_pairs():
    m = ShortTermMemory(4)
    for v in [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "x"}]},
        {"role": "tool", "tool_call_id": "x", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]:
        m.add(v)
    assert any(x.get("content") == "new" for x in m.items)
    assert len([x for x in m.items if x["role"] == "tool"]) == 1
    assert len([x for x in m.items if isinstance(x.get("content"), list)]) == 1


def test_waiting_survives_engine_restart(make_app):
    app, e, _ = make_app([[("ask_human", {"question": "Continue?"})]])
    e.store.enqueue("Ask")
    job = execute_next(e)
    app2, e2, _ = make_app(["Thanks"])
    assert e2.store.claim(e2.owner) is None
    e2.store.decide(
        job["id"],
        job["brain"]["pending_tools"][0]["call_id"],
        {"decision": "complete", "result": {"answer": "yes"}},
    )
    finished = execute_next(e2)
    assert finished["status"] == "succeeded", finished["error"]


def test_pause_after_completed_effect_does_not_rerun(make_app):
    app, e, adapter = make_app()
    job = e.store.enqueue("Write then pause")
    original = e.workspace_tools.file
    calls = []

    def write(*args, **kwargs):
        calls.append(1)
        result = original(*args, **kwargs)
        e.store.control(job["id"], "pause")
        return result

    e.workspace_tools.file = write
    adapter.replies = [
        [("write", {"path": "test.txt", "content": "exactly one write"})],
        "Finished",
    ]
    paused = execute_next(e)
    assert paused["status"] == "paused", paused["error"]
    e.store.control(job["id"], "resume")
    finished = execute_next(e)
    assert finished["status"] == "succeeded", finished["error"]
    assert len(calls) == 1


def test_two_gated_actions_approve_and_reject(make_app):
    app, e, adapter = make_app()
    skill = e.skills.build("gated", "gated", "gated")
    v = e.skills.create(
        skill["id"],
        "action_gated",
        "gated",
        "from fluxyr import params, output\noutput(params)",
        {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        requires_approval=True,
    )
    e.skills.test(v["id"], {"value": 0})
    e.skills.activate(v["id"])
    adapter.replies = [
        [("action_gated", {"value": 1}), ("action_gated", {"value": 2})],
        "Done",
    ]
    e.store.enqueue("Run")
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    pending = job["brain"]["pending_tools"]
    assert all(p["_status"] == "parked" for p in pending)
    e.store.decide(
        job["id"], pending[0]["call_id"], {"decision": "approve", "result": {}}
    )
    e.store.decide(
        job["id"], pending[1]["call_id"], {"decision": "reject", "reason": "No"}
    )
    finished = execute_next(e)
    assert finished["status"] == "succeeded", finished["error"]
    from fluxyr.models import Effect

    with e.db.transaction() as s:
        assert len(list(s.scalars(select(Effect)))) == 1


def test_python_test_job_can_wait_and_resume(make_app):
    app, e, _ = make_app()
    skill = e.skills.build("test-human", "test", "test")
    v = e.skills.create(
        skill["id"],
        "action_test_human",
        "test",
        'from fluxyr import request_human, output\noutput(request_human("City?"))',
        {"type": "object", "properties": {}},
    )
    c = app.test_client()
    response = c.post("/api/actions/" + v["id"] + "/test", json={"params": {}})
    assert response.status_code == 202
    job = execute_next(e)
    assert job["status"] == "waiting", job["error"]
    e.store.decide(
        job["id"],
        job["brain"]["pending_tools"][0]["call_id"],
        {"decision": "complete", "result": {"answer": "NY"}},
    )
    final = execute_next(e)
    assert final["status"] == "succeeded", final["error"]
    e.skills.activate(v["id"])


def test_oauth_refresh_serializes_and_preserves_rotating_token(make_app, monkeypatch):
    _, e, _ = make_app()
    if e.db.engine.dialect.name != "postgresql":
        pytest.skip("Row locks require PostgreSQL")
    e.vault.put(
        "oauth",
        "oauth2",
        {
            "client_id": "test",
            "client_secret": "secret",
            "token_url": "https://fixture.invalid/token",
            "refresh_token": "refresh-1",
            "expires_at": 0,
        },
    )
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["data"])
        time.sleep(0.1)

        class Response:
            ok = True

            def json(self):
                return {
                    "access_token": "new-access",
                    "refresh_token": "refresh-2",
                    "expires_in": 3600,
                }

        return Response()

    monkeypatch.setattr("fluxyr.vault.requests.post", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: e.vault.resolve("oauth"), range(2)))
    assert len(calls) == 1
    assert all(v["access_token"] == "new-access" for v in values)
    assert e.vault.get_optional("oauth")["refresh_token"] == "refresh-2"


def test_oauth_pkce_callback_consumes_state(make_app, monkeypatch):
    _, e, _ = make_app()
    e.vault.put(
        "oauth",
        "oauth2",
        {
            "client_id": "client",
            "authorization_url": "https://fixture.invalid/authorize",
            "token_url": "https://fixture.invalid/token",
        },
    )
    from urllib.parse import parse_qs, urlparse

    url = e.vault.start_oauth("oauth", "http://localhost/callback")
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]

    def token(content, data):
        assert data["code"] == "valid-code" and data["code_verifier"]
        return {
            **content,
            "access_token": "connected",
            "expires_at": time.time() + 3600,
        }

    monkeypatch.setattr(e.vault, "_token", token)
    e.vault.finish_oauth(query["state"][0], "valid-code")
    with pytest.raises(ValueError):
        e.vault.finish_oauth(query["state"][0], "valid-code")


def test_browser_and_techdoc_without_gateway(make_app, monkeypatch):
    from fluxyr.tools.techdoc.llm import adapter_factory, get_llm
    from fluxyr.tools.web import WebBrowserToolProvider

    _, e, adapter = make_app()
    provider = WebBrowserToolProvider()
    assert {x.name for x in provider.get_tools()} == {"web_browse", "web_extract"}
    token = adapter_factory.set(e.adapter)
    try:
        result = get_llm().complete(
            system="distill", user="GET /weather", max_tokens=1000
        )
        assert result.provider == "anthropic"
    finally:
        adapter_factory.reset(token)


def test_real_sdk_adapters_with_mock_http():
    import anthropic
    import httpx
    import openai

    from fluxyr.core.adapters.anthropic import AnthropicAdapter
    from fluxyr.core.adapters.openai import OpenAIAdapter

    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "test",
                    "content": [{"type": "text", "text": "Hello Anthropic"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello OpenAI"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 3,
                    "total_tokens": 11,
                },
            },
        )

    transport = httpx.MockTransport(handle)
    a = AnthropicAdapter(
        model="test",
        api_key="test",
        thinking_mode="none",
        thinking_budget=0,
        max_tokens=1024,
    )
    a.client = anthropic.Anthropic(
        api_key="test", http_client=httpx.Client(transport=transport)
    )
    o = OpenAIAdapter(model="test", api_key="test", max_tokens=1024)
    o.client = openai.OpenAI(
        api_key="test", http_client=httpx.Client(transport=transport)
    )
    for adapter in (a, o):
        response, calls, usage = adapter.execute_step_with_usage(
            messages=[{"role": "user", "content": "Hi"}], system_prompt="Test"
        )
        assert not calls and usage.total_tokens == 11
        assert "Hello" in json.dumps(response)
    assert captured[0]["max_tokens"] == 1024
    assert captured[1]["max_completion_tokens"] == 1024


def test_techdoc_llms_strategy_extracts_real_markdown(monkeypatch):
    import httpx

    from fluxyr.tools.techdoc.models import Fingerprint
    from fluxyr.tools.techdoc.strategies import llms_txt

    body = "# Authentication\nUse Bearer tokens in the Authorization header for every incoming API request.\n# Forecast\nGET /forecast?city=NY returns the weather forecast for the requested city name.\n# Errors\n429 means the request was rate limited and should be retried later."
    monkeypatch.setattr(
        llms_txt,
        "get_with_retry",
        lambda *_: httpx.Response(
            200, text=body, headers={"content-type": "text/plain"}
        ),
    )
    fp = Fingerprint(
        url="https://fixture.invalid/docs",
        platform="custom",
        confidence=1.0,
        signals={"llms_full_txt": "https://fixture.invalid/llms-full.txt"},
    )
    bundle = llms_txt.LlmsTxtStrategy().extract(fp, "Weather")
    assert bundle.page_count == 3
    assert any("/forecast" in p.markdown for p in bundle.pages)
