"""Public projection of pending requests and their explicitly attached previews."""

import copy
import mimetypes
from urllib.parse import quote


def open_request(entry):
    return (
        entry.get("_status") == "parked"
        and not entry.get("_decision")
        and (entry.get("_result") or {}).get("__pua__")
    )


def approval_view(job_id, session_id, entry):
    request_id = entry.get("_request_id") or entry["call_id"]
    base = f"/api/approvals/{quote(job_id, safe='')}/{quote(request_id, safe='')}"
    vault = entry.get("name") == "manage_vault_credential"
    private = entry.get("name") in ("browser_request_input", "browser_register_passkey")
    card = copy.deepcopy(entry["_result"]["__pua__"])
    assets = []
    for index, option in enumerate(card.get("payload", {}).get("choices", [])):
        if not isinstance(option, dict) or not option.get("path"):
            continue
        url = f"{base}/assets/{index}"
        option["url"] = url
        assets.append(
            {
                "choice_index": index,
                "label": option.get("label"),
                "preview_type": option.get("preview_type"),
                "mime_type": mimetypes.guess_type(option["path"])[0]
                or "application/octet-stream",
                "url": url,
            }
        )
    return {
        "job_id": job_id,
        "session_id": session_id,
        "request_id": request_id,
        "call_id": entry["call_id"],
        "tool_name": entry.get("name"),
        "kind": "vault_credential"
        if vault
        else "browser_passkey"
        if entry.get("name") == "browser_register_passkey"
        else "browser_private_input"
        if private
        else "human",
        "request": card,
        "assets": assets,
        "decision_url": f"{base}/decision",
        "submission_url": f"{base}/credential"
        if vault
        else f"{base}/private-input"
        if private
        else f"{base}/decision",
    }
