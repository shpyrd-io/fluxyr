import pytest

from fluxyr.tools.registry import Registry


def tools_for(engine):
    engine.store.enqueue("Inspect tool contracts")
    job = engine.store.claim(engine.owner)
    return {
        t["name"]: t
        for t in Registry(engine, job, lambda *_: None, lambda: False).definitions()
    }


@pytest.mark.parametrize(
    "args,path",
    [
        ({"question": "Which?", "choices": ["a" * 101, "Other"]}, "choices.0"),
        ({"question": "q" * 501}, "question"),
        ({"question": "Which?", "choices": [" ", "Other"]}, "choices.0"),
    ],
)
def test_ask_human_invalid_lengths_fail_before_execution(make_app, args, path):
    _, engine, _ = make_app()
    tool = tools_for(engine)["ask_human"]
    result, state = tool["function"](**args)
    assert state == "continue"
    assert result["type"] == "validation_error"
    assert result["executed"] is False
    assert path in result["error"]


def test_ask_human_unicode_boundaries_retain_question_and_choices(make_app):
    _, engine, _ = make_app()
    tool = tools_for(engine)["ask_human"]
    question, label = "é" * 500, "ç" * 100
    result, state = tool["function"](question=question, choices=[label, "Other"])
    assert state == "wait"
    pua = result["__pua__"]
    assert pua["payload"]["choices"] == [label, "Other"]
    assert question in str(pua)


@pytest.mark.parametrize(
    "config",
    [
        {"client_secret": "never-prefill"},
        {"client_id": "never-prefill"},
        {"refresh_token": "never-prefill"},
        {"token_auth_method": "unsupported"},
        {"token_url": "not-a-url"},
    ],
)
def test_oauth_prefill_rejects_private_or_invalid_fields(make_app, config):
    _, engine, _ = make_app()
    tool = tools_for(engine)["manage_vault_credential"]
    result, state = tool["function"](
        action="create",
        vault_item_type="oauth2",
        oauth_config=config,
    )
    assert state == "continue"
    assert result["type"] == "validation_error"
    assert result["executed"] is False
    assert engine.vault.list() == []
