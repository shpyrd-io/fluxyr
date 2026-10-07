import pytest

from fluxyr.models import ToolVersion
from fluxyr.runtime.contracts import validate_secret_references


@pytest.mark.parametrize(
    "source",
    [
        'from fluxyr import secret\nsecret("logical_name")',
        'from fluxyr import secret as credential\ncredential(name="logical_name")',
        'import fluxyr as f\nf.secret("logical_name")',
    ],
)
def test_secret_alias_mismatch_rejected_before_creating_candidate(make_app, source):
    _, e, _ = make_app()
    e.vault.put("Actual Vault name", "text", {"value": "private"})
    skill = e.skills.build("contract", "test", "test")
    with pytest.raises(ValueError, match="undeclared Vault names: logical_name"):
        e.skills.create(
            skill["id"],
            "action_contract",
            "test",
            source,
            {"type": "object"},
            secrets=["Actual Vault name"],
        )
    assert e.skills.get(skill["id"])["tools"] == []


def test_exact_secret_names_and_shapes_work_with_real_runner(make_app):
    _, e, _ = make_app()
    e.vault.put(
        "Actual Vault name",
        "key_password",
        {"key": "private-client-id", "password": "private-secret"},
    )
    skill = e.skills.build("contract", "test", "test")
    version = e.skills.create(
        skill["id"],
        "action_contract",
        "test",
        'from fluxyr import secret, output\nv=secret("Actual Vault name")\noutput({"configured": bool(v["key"] and v["password"])})',
        {"type": "object"},
        secrets=["Actual Vault name"],
    )
    assert e.skills.test(version["id"], {})["output"] == {"configured": True}
    e.skills.activate(version["id"])
    with pytest.raises(ValueError, match="Unknown Vault names"):
        e.skills.create(
            skill["id"],
            "action_missing",
            "test",
            'from fluxyr import secret\nsecret("Unknown")',
            {"type": "object"},
            secrets=["Unknown"],
        )


def test_caught_error_returned_as_success_false_cannot_pass_or_activate(make_app):
    _, e, _ = make_app()
    skill = e.skills.build("failure", "test", "test")
    version = e.skills.create(
        skill["id"],
        "action_failure",
        "test",
        'from fluxyr import output\ntry:\n raise RuntimeError("Integration unavailable")\nexcept RuntimeError as e:\n output({"success": False, "error": str(e)})',
        {"type": "object"},
    )
    result = e.skills.test(version["id"], {})
    assert (
        result["done"] is True
        and result["success"] is False
        and result["passed"] is False
    )
    assert result["error"] == "Integration unavailable" and result["exit_code"] == 0
    with pytest.raises(ValueError, match="passing test"):
        e.skills.activate(version["id"])
    # Existing records created by the previous false-positive test behavior are also blocked.
    with e.db.transaction() as s:
        row = s.get(ToolVersion, version["id"])
        row.state = "tested"
        row.test_result = {
            "success": True,
            "output": {"success": False, "error": "missing credential"},
        }
    with pytest.raises(ValueError, match="recorded test reports a failure"):
        e.skills.activate(version["id"])


def test_dynamic_names_keep_runtime_check_and_unrelated_secret_is_not_a_helper(
    make_app,
):
    validate_secret_references(
        'def secret(name): return name\nsecret("domain term")', []
    )
    _, e, _ = make_app()
    result = e.runner.run(
        {
            "id": "dynamic",
            "source": 'from fluxyr import secret, output\nname="not-declared"\noutput(secret(name))',
        },
        {},
        "dynamic-test",
    )
    assert result["success"] is False and "not declared" in result["error"]
