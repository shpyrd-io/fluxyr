"""Regression cases from the real workbench trace, with real runner execution."""

import json

import pytest

from fluxyr.inspection import (
    compact_inspection_history,
    execution_details,
    execution_list,
    skill_context,
)
from fluxyr.runtime.contracts import validate_secret_references
from fluxyr.tools.registry import normalize_human_args


@pytest.mark.parametrize(
    "source",
    [
        'from fluxyr import secret\nsecret("OAuth", force_refresh=False)',
        'from fluxyr import secret as s\ns("OAuth", force_refresh=True)',
        'import fluxyr as f\nf.secret("OAuth", force_refresh=True)',
        'from fluxyr import refresh_token\nrefresh_token("OAuth")',
        "from fluxyr import output\noutput()",
    ],
)
def test_unsupported_helpers_fail_before_generation_is_accepted(source):
    with pytest.raises(ValueError, match="Fluxyr helper"):
        validate_secret_references(source, ["OAuth"])


def test_failed_credential_resolution_never_runs_python(make_app):
    _, e, _ = make_app()
    # Missing authorization for a legitimate authorization-code credential.
    e.vault.put(
        "OAuth",
        "oauth2",
        {
            "client_id": "private-id",
            "token_url": "https://example.invalid/token",
            "authorization_url": "https://example.invalid/auth",
        },
    )
    version = {
        "id": "credential-failure",
        "source": 'from fluxyr import output, data_dir\n(data_dir / "executed.txt").write_text("executed")\noutput(True)',
        "secrets": ["OAuth"],
    }
    result = e.runner.run(version, {}, "diagnosis")
    assert result["executed"] is False and result["phase"] == "credential_resolution"
    assert result["credential"] == "OAuth" and result["side_effects_possible"] is False
    assert "Do not rebuild" in result["remediation"]
    assert "private-id" not in json.dumps(result)
    assert not (e.settings.data / "executed.txt").exists()
    # By contrast, a real exception from the action is correctly attributed.
    result = e.runner.run(
        {"id": "python-failure", "source": 'raise ValueError("invalid date")'},
        {},
        "diagnosis",
    )
    assert result["executed"] is True and result["phase"] == "python_execution"
    assert "invalid date" in result["error"]


def test_inspection_finds_recent_error_after_stream_and_pages_without_session_dump(
    make_app,
):
    _, e, _ = make_app()
    job = e.store.enqueue("first request")
    e.store.enqueue("unrelated-session-message")
    for i in range(510):
        e.store.emit(job["session_id"], job["id"], "reasoning", {"text": "fragment"})
    for i in range(25):
        e.store.emit(
            job["session_id"],
            job["id"],
            "tool_end",
            {
                "tool_name": "test_action",
                "tool_call_id": str(i),
                "result": {
                    "error": "OAuth setup",
                    "phase": "credential_resolution",
                    "executed": False,
                },
            },
        )
    view = execution_details(e, job["id"])
    assert len(view["events"]) == 20 and view["next_before_event_id"]
    assert all(v["type"] == "tool_end" for v in view["events"])
    assert view["events"][-1]["payload"]["result"]["phase"] == "credential_resolution"
    assert "unrelated-session-message" not in json.dumps(view)
    earlier = execution_details(
        e, job["id"], before_event_id=view["next_before_event_id"]
    )
    assert {v["id"] for v in earlier["events"]}.isdisjoint(
        v["id"] for v in view["events"]
    )
    assert "brain" not in view["job"] and "input" not in view["job"]
    assert len(json.dumps(view)) < 20000
    listed = execution_list(e, limit=1)
    assert len(listed["executions"]) == 1 and listed["next_before"]
    assert (
        execution_list(e, limit=1, before=listed["next_before"])["executions"][0]["id"]
        == job["id"]
    )


def test_skill_catalogue_omits_source_and_reads_specific_version(make_app):
    _, e, _ = make_app()
    skill = e.skills.build("integration", "description", "instruction")
    versions = [
        e.skills.create(
            skill["id"],
            "integration",
            "test",
            f"from fluxyr import output\noutput({i})",
            {"type": "object"},
        )
        for i in range(7)
    ]
    catalogue = skill_context(e)
    assert "source" not in json.dumps(catalogue) and "instruction" not in catalogue[0]
    tool = catalogue[0]["tools"][0]
    assert tool["version_count"] == 7 and len(tool["versions"]) == 5
    view = skill_context(
        e, skill_id=skill["id"], include_source=True, version_id=versions[0]["id"]
    )
    assert view["tools"][0]["source"] == versions[0]["source"]
    assert view["tools"][0]["source_version_id"] == versions[0]["id"]


def test_human_choices_accept_the_observed_key_value_shape_without_retry():
    args = normalize_human_args(
        {
            "question": "Environment?",
            "choices": [
                {"key": "prod", "value": "Production"},
                {"key": "sandbox", "value": "Sandbox"},
            ],
        }
    )
    assert args["choices"] == ["Production", "Sandbox"]
    rich = {
        "question": "Select?",
        "choices": [{"label": "one", "content": "private preview"}],
    }
    assert normalize_human_args(rich) == rich  # never discard meaningful rich content


def test_legacy_inspection_is_bounded_without_losing_user_or_action_evidence():
    raw = json.dumps(
        {"job": {"id": "job-1", "brain": "x" * 100000}, "error": "OAuth failure"}
    )
    messages = [
        {
            "role": "tool",
            "name": "inspect_execution",
            "tool_call_id": "call-1",
            "content": raw,
        },
        {"role": "user", "content": raw},
        {"role": "tool", "name": "action_report", "content": raw},
    ]
    compact_inspection_history(messages)
    assert len(messages[0]["content"]) < 1000
    assert "OAuth failure" in messages[0]["content"]
    assert messages[0]["tool_call_id"] == "call-1"
    assert messages[1]["content"] == messages[2]["content"] == raw
