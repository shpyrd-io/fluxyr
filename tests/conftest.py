import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from fluxyr_agent.app import create_app
from fluxyr_agent.config import Settings
from fluxyr_agent.core.adapters.base import AIProviderAdapter
from fluxyr_agent.core.utils.token_usage import TokenUsage


class ScriptedAdapter(AIProviderAdapter):
    def __init__(self, replies):
        super().__init__("test-model")
        self.replies = list(replies)
        self.calls = []

    def get_provider_name(self):
        return "anthropic"

    def get_token_usage(self):
        return TokenUsage(10, 5, "anthropic")

    def format_messages(self, m):
        return m

    def format_tools(self, t):
        return t

    def execute_step(
        self,
        messages,
        system_prompt,
        tools=None,
        stream=False,
        stream_callback=None,
        tool_choice=None,
    ):
        if not tools or any(t.get("name") == "store_semantic_memory" for t in tools):
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": "{}"}],
            }, []
        self.calls.append(messages.copy())
        reply = self.replies.pop(0) if self.replies else "Done"
        if callable(reply):
            reply = reply(messages, tools)
        if isinstance(reply, str):
            if stream_callback:
                stream_callback(
                    {"type": "content_block_delta", "delta": {"text": reply}}, None
                )
            return {
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
            }, []
        calls = []
        blocks = []
        for i, (name, args) in enumerate(reply):
            call_id = f"call_{len(self.calls)}_{i}"
            calls.append({"name": name, "arguments": args, "call_id": call_id})
            blocks.append(
                {"type": "tool_use", "id": call_id, "name": name, "input": args}
            )
        return {"role": "assistant", "content": blocks}, calls


@pytest.fixture
def database_url(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        yield "sqlite:///" + str(tmp_path / "test.sqlite")
        return
    # Each test uses a new schema in an explicitly designated disposable database.
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    parsed = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    yield parsed.render_as_string(hide_password=False)
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


@pytest.fixture
def make_app(tmp_path, database_url):
    apps = []

    def factory(replies=(), **kwargs):
        adapter = ScriptedAdapter(replies)
        app = create_app(
            Settings(
                database_url=database_url,
                root=tmp_path / "instance",
                testing=True,
                **kwargs,
            ),
            lambda: adapter,
        )
        apps.append(app)
        return app, app.extensions["engine"], adapter

    yield factory
    for app in apps:
        e = app.extensions["engine"]
        e.stop()
        e.db.engine.dispose()


def execute_next(engine):
    job = engine.store.claim(engine.owner)
    assert job is not None
    engine.execute(job)
    from fluxyr_agent.database import row_dict
    from fluxyr_agent.models import Job

    with engine.db.transaction() as s:
        return row_dict(s.get(Job, job["id"]))
