"""Vault cards carry metadata only; private form saves never enter brain state."""

from jsonschema import validate
from sqlalchemy import select

from ..models import Decision, Job, VaultItem
from ..vault import TYPES
from .envelope import build_approval_pua

OAUTH_PREFILL_SCHEMA = {
    "type": "object",
    "description": "Public OAuth settings from the provider documentation, prefilled for user review. Never include client IDs, secrets, tokens or certificate contents.",
    "properties": {
        "token_url": {
            "type": "string",
            "maxLength": 2048,
            "pattern": r"^https?://\S+$",
        },
        "authorization_url": {
            "type": "string",
            "maxLength": 2048,
            "pattern": r"^https?://\S+$",
        },
        "scope": {
            "type": "string",
            "maxLength": 2000,
            "description": "Space-separated OAuth scopes.",
        },
        "token_auth_method": {
            "type": "string",
            "enum": ["client_secret_post", "client_secret_basic"],
        },
    },
    "additionalProperties": False,
}


def credential_request(
    vault,
    action,
    vault_item_type=None,
    suggested_name=None,
    vault_item_id=None,
    oauth_grant_type=None,
    mtls_certificate_id=None,
    oauth_config=None,
):
    if oauth_config is not None:
        validate(oauth_config, OAUTH_PREFILL_SCHEMA)
    if action == "create":
        if vault_item_type not in TYPES:
            raise ValueError(
                "Choose a supported vault_item_type for the new credential"
            )
        if vault_item_id:
            raise ValueError("Use edit for an existing Vault item")
        item = {
            "vault_item_type": vault_item_type,
            "suggested_name": suggested_name or "",
        }
    elif action == "edit":
        with vault.db.transaction() as session:
            existing = session.get(VaultItem, vault_item_id) if vault_item_id else None
            if not existing:
                raise ValueError(
                    "List Vault items and supply the existing vault_item_id"
                )
            if vault_item_type and vault_item_type != existing.type:
                raise ValueError("An existing credential cannot change type")
            item = {
                "vault_item_id": existing.id,
                "vault_item_type": existing.type,
                "vault_item_name": existing.name,
                "suggested_name": suggested_name or existing.name,
            }
            if existing.type == "oauth2":
                item["oauth_config"] = vault.oauth_configuration(existing, session)
    else:
        raise ValueError("Credential requests support create or edit")
    if oauth_grant_type or mtls_certificate_id or oauth_config is not None:
        if item["vault_item_type"] != "oauth2":
            raise ValueError("OAuth options require an OAuth credential")
        config = item.setdefault("oauth_config", {})
        config.update(oauth_config or {})
        if oauth_grant_type:
            if oauth_grant_type not in ("authorization_code", "client_credentials"):
                raise ValueError("Unsupported OAuth grant type")
            config["grant_type"] = oauth_grant_type
        if mtls_certificate_id:
            with vault.db.transaction() as session:
                certificate = session.get(VaultItem, mtls_certificate_id)
                if not certificate or certificate.type not in (
                    "certificate_pem",
                    "certificate_pfx",
                ):
                    raise ValueError(
                        "Choose an existing certificate ID from vault_list"
                    )
            config["certificate_id"] = mtls_certificate_id
    return {
        "__pua__": build_approval_pua(
            "confirm-reject",
            "Create credential" if action == "create" else "Edit credential",
            "Enter sensitive values directly in Vault. The agent receives only the saved item reference.",
            kind="vault_credential",
            action=action,
            **item,
        )
    }, "wait"


def save_credential(engine, job_id, request_id, body):
    """Encrypt and save, then resume atomically. Never persist or echo the body."""
    if not isinstance(body, dict) or set(body) - {"name", "kind", "content"}:
        raise ValueError("Invalid credential form")
    with engine.db.transaction() as session:
        job = session.get(Job, job_id, with_for_update=True)
        if not job:
            raise ValueError("Job not found")
        old = session.scalar(
            select(Decision).where(
                Decision.job_id == job_id, Decision.call_id == request_id
            )
        )
        if old:
            result = old.value.get("result") or {}
            if old.value.get("decision") != "complete" or not result.get(
                "vault_item_id"
            ):
                raise ValueError("This request has already been resolved")
            # Lost HTTP response/repeated Save: never replace credentials a second time.
            return {"duplicate": True, "status": job.status, "item": result}
        if job.status != "waiting":
            raise ValueError("Job is not waiting for a credential")
        entry = next(
            (
                p
                for p in (job.brain or {}).get("pending_tools", [])
                if (p.get("_request_id") or p.get("call_id")) == request_id
            ),
            None,
        )
        if (
            not entry
            or entry.get("name") != "manage_vault_credential"
            or entry.get("_status") != "parked"
            or entry.get("_decision")
        ):
            raise ValueError("Credential request is no longer current")
        payload = entry["_result"]["__pua__"]["payload"]
        kind, name, content = body.get("kind"), body.get("name"), body.get("content")
        if (
            kind != payload["vault_item_type"]
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or not isinstance(content, dict)
        ):
            raise ValueError(
                "Name, credential type and fields must match the Vault form"
            )
        # Avoid putting values from parser/validation exceptions into HTTP errors.
        try:
            if payload["action"] == "create":
                item = engine.vault._put(session, name, kind, content, create_only=True)
            else:
                existing = session.get(
                    VaultItem, payload["vault_item_id"], with_for_update=True
                )
                if not existing or existing.type != kind:
                    raise ValueError("The requested credential is no longer available")
                item = engine.vault._update(session, existing.id, name, content)
        except (ValueError, KeyError, TypeError):
            raise ValueError(
                "Could not save credential. Check its name, required fields and credential format/settings; create requires a unique name."
            ) from None
        safe_result = {
            "action": payload["action"],
            "vault_item_id": item["id"],
            "vault_item_name": item["name"],
        }
        result = engine.store._record_decision(
            session,
            job,
            entry,
            request_id,
            {"decision": "complete", "result": safe_result},
        )
        return {**result, "item": safe_result}
