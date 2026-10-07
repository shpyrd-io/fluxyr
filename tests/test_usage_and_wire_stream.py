from decimal import Decimal
from types import SimpleNamespace as NS

from fluxyr_agent.activity import ActivityEmitter
from fluxyr_agent.core.adapters.openai_execution import execute_stream
from fluxyr_agent.models import Routine
from fluxyr_agent.streaming import StreamRecorder
from fluxyr_agent.usage import bind_usage, usage_report


def test_wire_arguments_are_visible_before_call_id_and_counted_once():
    seen = []
    record = StreamRecorder(lambda kind, data: seen.append((kind, data)))

    def chunks():
        for index, fragment in enumerate(['{"source":', '"print(42)"', "}"]):
            yield NS(
                id="request",
                choices=[
                    NS(
                        finish_reason=None,
                        delta=NS(
                            tool_calls=[
                                NS(
                                    index=0,
                                    id="call" if index == 2 else None,
                                    function=NS(
                                        name="create_action", arguments=fragment
                                    ),
                                )
                            ]
                        ),
                    )
                ],
            )
            assert sum(kind == "tool_argument_delta" for kind, _ in seen) == index + 1

    client = NS(chat=NS(completions=NS(create=lambda **_: chunks())))
    _, calls = execute_stream(client, {}, record)
    assert calls[0]["arguments"] == {"source": "print(42)"}
    assert calls[0]["call_id"] == "call"
    assert sum(p["characters"] for k, p in seen if k == "tool_argument_delta") == len(
        '{"source":"print(42)"}'
    )
    assert all("partial_json" not in p for k, p in seen if k == "tool_argument_delta")


def test_usage_captures_provider_cost_and_aggregates_descendants_once(make_app):
    _, e, _ = make_app()
    parent = e.store.enqueue("Build")
    child = e.store.enqueue(
        "Child", session_id=None, inputs={"parent_job_id": parent["id"]}
    )
    emit = ActivityEmitter(e.store, child)
    adapter = bind_usage(NS(), emit)
    emit("model_start", {})
    raw = {
        'provider': "openrouter",
        'model': "minimax/minimax-m3",
        'request_id': "req1",
        'input_tokens': 10,
        'cache_read_tokens': 5,
        'output_tokens': 7,
        'upstream_cost_micros': Decimal("1234.5"),
    }
    adapter._usage_callback(raw)
    adapter._usage_callback(raw)  # duplicate provider callback/replay must not add cost
    emit("model_end", {})
    emit("model_start", {})
    emit("model_end", {})  # failed/empty response: unknown, never claim free
    report = usage_report(e.db)
    assert report["total"]["tokens"] == 22
    assert report["total"]["cost_usd"] == 0.0012345
    assert report["total"]["requests"] == 2
    assert report["total"]["missing_cost"] == 1
    assert report["by_job"][parent["id"]] == report["by_job"][child["id"]]
    assert report["by_job"][child["id"]] == report["total"]
    assert len(report["by_call"]) == 2


def test_routine_prose_decodes_accents_on_read_write_and_execution(make_app):
    from fluxyr_agent.text import prose_unicode

    _, e, _ = make_app()
    routine = e.routines.put(
        {"name": r"A\u00e7\u00e3o", "prompt": r"Ol\u00e1 \ud83c\udfb2"}
    )
    assert routine["name"] == "Ação" and routine["prompt"] == "Olá 🎲"
    with e.db.transaction() as s:
        s.get(Routine, routine["id"]).prompt = r"Substitui\u00e7\u00e3o"
    assert e.routines.list()[0]["prompt"] == "Substituição"
    assert e.routines.run(routine["id"])["prompt"] == "Substituição"
    assert (
        prose_unicode(r"Keep \u000a \u0061 \ud800 C:\new")
        == r"Keep \u000a \u0061 \ud800 C:\new"
    )
