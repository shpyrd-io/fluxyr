import copy

import pytest
from conftest import execute_next

from fluxyr.config import Settings
from fluxyr.interactions.human import action_wait
from fluxyr.models import Job

AUTH = {"Authorization": "Bearer test-approval-key"}


def waiting(engine):
    engine.store.enqueue("Choose an environment")
    return execute_next(engine)


def test_approval_auth_env_optional_and_not_exposed(monkeypatch, make_app):
    monkeypatch.delenv("FLUXYR_APPROVALS_API_KEY", raising=False)
    assert Settings().approvals_api_key == ""
    monkeypatch.setenv("FLUXYR_APPROVALS_API_KEY", "env-secret")
    assert Settings().approvals_api_key == "env-secret"
    assert "env-secret" not in repr(Settings())
    app, _, _ = make_app()
    client = app.test_client()
    assert client.get("/api/approvals").status_code == 401
    assert "env-secret" not in client.get("/api/settings").text
    assert (
        client.get(
            "/api/approvals", headers={"Authorization": "Bearer env-secret"}
        ).json
        == []
    )


@pytest.mark.parametrize("key", ["", "test-approval-key"])
def test_approval_list_and_decisions_auth_and_retry(make_app, key):
    app, engine, _ = make_app(
        [
            [
                (
                    "ask_human",
                    {"question": "Environment?", "choices": ["Production", "Sandbox"]},
                )
            ]
        ],
        approvals_api_key=key,
    )
    job = waiting(engine)
    client = app.test_client()
    headers = AUTH if key else {}
    if key:
        for auth in (
            {},
            {"Authorization": "Bearer wrong"},
            {"Authorization": "Basic test-approval-key"},
        ):
            response = client.get(
                "/api/approvals?token=test-approval-key", headers=auth
            )
            assert response.status_code == 401
            assert response.json["code"] == "approval_auth_required"
            assert response.headers["WWW-Authenticate"].startswith("Bearer")
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/files").status_code == 200
    row = client.get("/api/approvals", headers=headers).json[0]
    assert row["job_id"] == job["id"] and row["assets"] == []
    assert row["request"]["payload"]["choices"] == ["Production", "Sandbox"]
    body = {"decision": "complete", "result": {"selected_index": 0}}
    legacy = f"/api/jobs/{job['id']}/decisions/{row['request_id']}"
    if key:
        for url in (row["decision_url"], legacy):
            assert client.post(url, json=body).status_code == 401
    assert client.post(row["decision_url"], json=body, headers=headers).json == {
        "status": "queued",
        "remaining": 0,
    }
    assert client.get("/api/approvals", headers=headers).json == []
    assert client.post(legacy, json=body, headers=headers).json["duplicate"]
    assert (
        client.post(legacy, json={"decision": "reject"}, headers=headers).status_code
        == 400
    )


def test_vault_submission_aliases_require_key_and_cross_origin_stays_blocked(make_app):
    app, engine, _ = make_app(
        [
            [
                (
                    "manage_vault_credential",
                    {
                        "action": "create",
                        "vault_item_type": "key_password",
                        "suggested_name": "Example",
                    },
                )
            ]
        ],
        approvals_api_key="test-approval-key",
    )
    job = waiting(engine)
    client = app.test_client()
    row = client.get("/api/approvals", headers=AUTH).json[0]
    assert row["kind"] == "vault_credential"
    body = {
        "name": "Example",
        "kind": "key_password",
        "content": {"key": "private", "password": "secret"},
    }
    legacy = f"/api/jobs/{job['id']}/vault/{row['request_id']}"
    for url in (row["submission_url"], legacy):
        assert client.post(url, json=body).status_code == 401
        assert (
            client.post(
                url, json=body, headers={**AUTH, "Origin": "https://elsewhere.invalid"}
            ).status_code
            == 403
        )
    assert (
        client.post(
            row["decision_url"], json={"decision": "approve"}, headers=AUTH
        ).status_code
        == 400
    )
    saved = client.post(row["submission_url"], json=body, headers=AUTH)
    assert saved.status_code == 200, saved.json
    assert "secret" not in saved.text
    assert client.post(legacy, json=body, headers=AUTH).json["duplicate"]


def test_approval_assets_are_bound_to_current_request_and_saved_choice(
    make_app, tmp_path
):
    app, engine, _ = make_app(
        [[("ask_human", {"question": "Choose", "choices": ["Card", "File"]})]],
        approvals_api_key="test-approval-key",
    )
    job = waiting(engine)
    (engine.settings.data / "image.png").write_bytes(b"test-image")
    result = action_wait(
        {
            "waiting": {
                "request": {
                    "key": "card",
                    "title": "Choose a card",
                    "variant": "choices",
                    "choices": [
                        {
                            "label": "HTML",
                            "preview_type": "html",
                            "content": "<h1>Preview</h1>",
                        },
                        {
                            "label": "Image",
                            "preview_type": "image",
                            "path": "image.png",
                        },
                    ],
                }
            }
        },
        engine.files,
    )
    with engine.db.transaction() as s:
        dbjob = s.get(Job, job["id"])
        brain = copy.deepcopy(dbjob.brain)
        entry = brain["pending_tools"][0]
        entry["_result"] = result
        entry["_request_id"] = "round-two"
        dbjob.brain = brain
    client = app.test_client()
    row = client.get("/api/approvals", headers=AUTH).json[0]
    assert row["request_id"] == "round-two"
    assert len(row["assets"]) == 2
    for asset in row["assets"]:
        assert client.get(asset["url"]).status_code == 401
        response = client.get(asset["url"], headers=AUTH)
        assert response.status_code == 200
        assert response.mimetype == asset["mime_type"]
        assert response.headers["Cache-Control"] == "no-store"
        assert "sandbox allow-scripts" in response.headers["Content-Security-Policy"]
        assert (
            row["request"]["payload"]["choices"][asset["choice_index"]]["url"]
            == asset["url"]
        )
    assert client.get(row["assets"][0]["url"], headers=AUTH).data == b"<h1>Preview</h1>"
    assert (
        client.get(
            row["assets"][0]["url"].replace("/assets/0", "/assets/99"), headers=AUTH
        ).status_code
        == 404
    )
    # A saved path pointing through a symlink outside data is still rejected.
    outside = tmp_path / "outside.txt"
    outside.write_text("not shared")
    (engine.settings.data / "image.png").unlink()
    (engine.settings.data / "image.png").symlink_to(outside)
    assert client.get(row["assets"][1]["url"], headers=AUTH).status_code == 400
    assert (
        client.post(
            row["decision_url"], json={"decision": "reject"}, headers=AUTH
        ).status_code
        == 200
    )
    assert client.get(row["assets"][0]["url"], headers=AUTH).status_code == 404
