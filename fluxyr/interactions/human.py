"""Typed human cards and durable per-invocation continuation, backed by jobs."""

import copy
import uuid
from urllib.parse import quote

from ..runtime.human_protocol import MAX_ROUNDS, normalize_request, response_for
from .envelope import build_approval_pua


def human(question, choices=None, preflight=False):
    request = normalize_request(
        {
            "key": "workbench",
            "title": question[:100],
            "message": question[:500],
            "variant": "confirm-reject"
            if preflight
            else "choices"
            if choices
            else "continue",
            "choices": choices or [],
        }
    )
    payload = card(request)
    payload["__pua__"]["payload"]["preflight"] = preflight
    if choices:
        payload["__pua__"]["payload"]["choices"] = choices
    return payload, "wait"


def card(request):
    return {
        "__pua__": build_approval_pua(
            request["variant"],
            request["title"],
            request.get("message"),
            choices=request.get("choices", []),
            placeholder=request.get("placeholder"),
        ),
        "question": request["title"],
    }


def action_wait(result, files):
    waiting = result["waiting"]
    # Old script versions emitted only a question. The SDK now emits typed data.
    request = normalize_request(
        waiting.get("request")
        or {
            "key": "question_1",
            "title": waiting["question"][:100],
            "variant": "continue",
        }
    )
    for option in request.get("choices", []):
        if option.get("preview_type") == "html" and option.get("content") is not None:
            path = ".previews/" + uuid.uuid4().hex + ".html"
            file = files.path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(option.pop("content"), encoding="utf-8")
            option["path"] = path
        if option.get("path"):
            path = files.path(option["path"])
            if not path.is_file():
                raise ValueError("Choice preview file does not exist")
            option["url"] = "/preview/" + quote(str(path.relative_to(files.root)))
        elif option.get("preview_type") in ("image", "file"):
            raise ValueError("Image/file choices need a local path under data_dir")
    responses = waiting.get("responses", [])
    if len(responses) >= MAX_ROUNDS:
        raise ValueError("Too many human interaction rounds")
    return {
        **card(request),
        **{k: result[k] for k in ("version_id", "source_sha256") if k in result},
        "__human__": {"request": request, "responses": responses},
    }


def request_for(entry):
    prior = entry.get("_result") or {}
    if prior.get("__human__"):
        return prior["__human__"]["request"]
    pua = prior.get("__pua__") or {}
    payload = pua.get("payload") or {}
    return normalize_request(
        {
            "key": "workbench",
            "title": pua.get("title") or prior.get("question") or "Human input",
            "variant": "confirm-reject"
            if payload.get("preflight")
            else payload.get("variant", "continue"),
            "choices": payload.get("choices")
            or payload.get("metadata", {}).get("choices", []),
        }
    )


def continuation_for(entry):
    prior = (entry.get("_result") or {}).get("__human__")
    if not prior:
        return None
    replies = copy.deepcopy(prior.get("responses", []))
    if len(replies) >= MAX_ROUNDS:
        raise ValueError("Too many human interaction rounds")
    replies.append(
        {
            "request": {k: prior["request"][k] for k in ("key", "variant")},
            "response": response_for(prior["request"], entry["_decision"]),
        }
    )
    return {"responses": replies}


def repark(entry, result):
    entry["_result"] = result
    entry["_request_id"] = str(uuid.uuid4())
    entry.pop("_decision", None)
    entry.pop("_reexec", None)
