from types import SimpleNamespace

import pytest
from sqlalchemy import select

from fluxyr_agent.core.adapters.openai_stream_translator import OpenAIStreamTranslator
from fluxyr_agent.models import Effect, ToolVersion
from fluxyr_agent.streaming import StreamRecorder
from fluxyr_agent.tools.registry import Registry, decode_payloads


def test_provider_reasoning_text_and_block_boundaries_are_persisted():
    events = []
    record = StreamRecorder(lambda kind, payload: events.append((kind, payload)))
    translator = OpenAIStreamTranslator()
    for delta, finish in [
        ({"reasoning": "Inspecting inputs"}, None),
        ({"content": "## Result\n"}, None),
        ({"content": "**Ready**"}, "stop"),
    ]:
        chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(delta=SimpleNamespace(**delta), finish_reason=finish)
            ]
        )
        for event in translator.consume(chunk):
            record(event)
    assert [p["text"] for kind, p in events if kind == "reasoning"] == [
        "Inspecting inputs"
    ]
    assert (
        "".join(p["text"] for kind, p in events if kind == "delta")
        == "## Result\n**Ready**"
    )
    opened = [p["block_id"] for kind, p in events if kind == "stream_open"]
    assert len(set(opened)) == 2
    assert opened == [p["block_id"] for kind, p in events if kind == "stream_close"]
    # The next model step reuses index 0, but must not append to the prior step.
    record(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "Next step"},
        }
    )
    assert events[-1][1]["block_id"] not in opened
    # Opaque signatures/redacted reasoning are not displayable content.
    count = len(events)
    record(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "opaque"},
        }
    )
    assert len(events) == count


@pytest.mark.parametrize(
    "name,canonical",
    [
        ("pick_topic", "action_pick_topic"),
        ("action_pick_topic", "action_pick_topic"),
        ("Clima - Previsão", "action_clima_previsao"),
    ],
)
def test_action_creation_returns_real_callable_name(make_app, name, canonical):
    _, e, _ = make_app()
    skill = e.skills.build("topic", "Topics", "Pick a topic")
    candidate = e.skills.create(
        skill["id"],
        name,
        "topic",
        "from fluxyr import output\noutput(42)",
        {"type": "object"},
    )
    assert candidate["name"] == candidate["function_name"] == canonical
    assert e.skills.test(candidate["id"], {})["output"] == 42
    assert e.skills.activate(candidate["id"])["name"] == canonical


def test_bad_candidate_is_rejected_before_effect_claim_and_can_be_corrected(make_app):
    _, e, _ = make_app()
    skill = e.skills.build("topic", "Topics", "Pick a topic")
    e.skills.update(skill["id"], spec="## Pick topic\nReturn a topic.")
    e.builds.enqueue(skill["id"])
    job = e.store.claim(e.owner)
    e.builds.plan(
        job["id"], skill["id"], [{"name": "pick_topic", "description": "Pick a topic"}]
    )
    registry = Registry(e, job, lambda *_: None, lambda: False)
    create = next(
        t["function"] for t in registry.definitions() if t["name"] == "create_action"
    )
    args = dict(
        skill_id=skill["id"],
        name="pick_topic",
        description="topic",
        source="return 42",
        parameters={"type": "object", "properties": {}},
    )
    # ast.parse alone accepts a top-level return; compilation must reject it.
    result, _ = create(**args)
    assert result["type"] == "validation_error" and result["executed"] is False
    with e.db.transaction() as s:
        assert list(s.scalars(select(Effect))) == []
        assert list(s.scalars(select(ToolVersion))) == []
    result, _ = create(**{**args, "source": "from fluxyr import output\noutput(42)"})
    assert result["function_name"] == "action_pick_topic"


def test_normalized_name_collision_does_not_update_another_skill(make_app):
    _, e, _ = make_app()
    a = e.skills.build("A", "A", "A")
    b = e.skills.build("B", "B", "B")
    e.skills.create(a["id"], "Pick topic", "A", "pass", {"type": "object"})
    with pytest.raises(ValueError, match="another skill"):
        e.skills.create(b["id"], "pick_topic", "B", "pass", {"type": "object"})


def test_invalid_test_inputs_do_not_run_python_or_claim_effect(make_app):
    _, e, _ = make_app()
    skill = e.skills.build("booleans", "booleans", "test")
    version = e.skills.create(
        skill["id"],
        "booleans",
        "test",
        "from fluxyr import output\noutput(1)",
        {"type": "object", "properties": {"flag": {"type": "boolean"}}},
    )
    e.store.enqueue("Test")
    job = e.store.claim(e.owner)
    registry = Registry(e, job, lambda *_: None, lambda: False)
    test = next(
        t["function"] for t in registry.definitions() if t["name"] == "test_action"
    )
    result, _ = test(version_id=version["id"], params={"flag": "false"})
    assert result["type"] == "validation_error" and result["executed"] is False
    with e.db.transaction() as s:
        assert not list(s.scalars(select(Effect)))
    assert not (e.settings.runtime / "envs").exists()


def test_json_text_payload_preserves_numbers_booleans_and_arrays(make_app):
    _, e, _ = make_app()
    skill = e.skills.build("typed", "typed", "test")
    e.skills.update(skill["id"], spec="## Typed\nEcho integer and boolean input.")
    e.builds.enqueue(skill["id"])
    job = e.store.claim(e.owner)
    e.builds.plan(job["id"], skill["id"], [{"name": "typed", "description": "Echo"}])
    registry = Registry(e, job, lambda *_: None, lambda: False)
    functions = {t["name"]: t["function"] for t in registry.definitions()}
    candidate, _ = functions["create_action"](
        skill_id=skill["id"],
        name="typed",
        description="Echo typed input",
        source="from fluxyr import params, output\noutput(params)",
        parameters_json='{"type":"object","properties":{"count":{"type":"integer"},"enabled":{"type":"boolean"}},"required":["count","enabled"]}',
    )
    e.store.enqueue("Test the generated action")
    main_job = e.store.claim(e.owner)
    registry = Registry(e, main_job, lambda *_: None, lambda: False)
    functions = {t["name"]: t["function"] for t in registry.definitions()}
    result, _ = functions["test_action"](
        version_id=candidate["id"], params_json='{"count":4,"enabled":false}'
    )
    assert result["passed"]
    assert type(result["output"]["count"]) is int
    assert result["output"]["enabled"] is False
    report, _ = functions["finish_execution"](
        output_json='[{"count":4,"enabled":false}]',
        evidence="Actual typed result",
    )
    assert registry.report["output"] == [{"count": 4, "enabled": False}]
    assert (
        decode_payloads({"output_json": "null"}, {"output": None}, ["output"])["output"]
        is None
    )
    for payload in (
        {"params_json": "not JSON"},
        {"params_json": "[]"},
        {"params_json": "{}", "params": {}},
    ):
        result, _ = functions["test_action"](version_id=candidate["id"], **payload)
        assert result["type"] == "validation_error" and result["executed"] is False
