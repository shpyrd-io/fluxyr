"""Pure interaction contract, shared with the standalone action SDK."""

import json

MAX_ROUNDS = 5
MAX_TITLE_LENGTH = 100
MAX_MESSAGE_LENGTH = 500
MAX_CHOICE_LABEL_LENGTH = 100


def normalize_request(request):
    r = dict(request)
    if r.get("variant") not in ("continue", "choices", "confirm-reject"):
        raise ValueError("Unknown human interaction variant")
    for field, limit in [("key", 100), ("title", MAX_TITLE_LENGTH)]:
        if (
            not isinstance(r.get(field), str)
            or not r[field].strip()
            or len(r[field]) > limit
        ):
            raise ValueError(f"{field} must contain 1–{limit} characters")
    for field, limit in [("message", MAX_MESSAGE_LENGTH), ("placeholder", 200)]:
        if r.get(field) is not None and (
            not isinstance(r[field], str) or len(r[field]) > limit
        ):
            raise ValueError(f"{field} must be text up to {limit} characters")
    if not isinstance(r.get("context", {}), dict):
        raise ValueError("Interaction context must be an object")
    if r["variant"] == "choices":
        options = r.get("choices")
        if not isinstance(options, list) or not 2 <= len(options) <= 6:
            raise ValueError("Provide 2–6 choices")
        normalized = []
        for candidate in options:
            if not isinstance(candidate, (str, dict)):
                raise ValueError("Choices must be labels or objects")
            c = (
                {"label": candidate}
                if isinstance(candidate, str)
                else {
                    k: v
                    for k, v in candidate.items()
                    if k
                    in (
                        "label",
                        "value",
                        "description",
                        "preview_type",
                        "content",
                        "path",
                        "mime_type",
                    )
                }
            )
            if (
                not isinstance(c.get("label"), str)
                or not c["label"].strip()
                or len(c["label"]) > MAX_CHOICE_LABEL_LENGTH
            ):
                raise ValueError(
                    f"Each choice needs a label up to {MAX_CHOICE_LABEL_LENGTH} characters"
                )
            if c.get("preview_type") not in (None, "text", "html", "image", "file"):
                raise ValueError("Unsupported choice preview type")
            if c.get("content") is not None and not isinstance(c["content"], str):
                raise ValueError("Preview content must be a string")
            if c.get("path") is not None and not isinstance(c["path"], str):
                raise ValueError("Preview path must be a string relative to data_dir")
            normalized.append(c)
        r["choices"] = normalized
    elif r.get("choices"):
        raise ValueError("Choices are only valid for a choice interaction")
    if len(json.dumps(r).encode()) > 64000:
        raise ValueError("Interaction exceeds 64 KB; put large previews in data_dir")
    return r


def response_for(request, decision):
    """Validate user input against the saved request, never regenerated options."""
    kind = decision.get("decision")
    context = request.get("context", {})
    if kind == "reject":
        return {
            "decision": "rejected",
            "reason": decision.get("reason") or "",
            "context": context,
        }
    result = decision.get("result") or {}
    variant = request["variant"]
    if variant == "confirm-reject":
        if kind != "approve":
            raise ValueError("This request needs approve or reject")
        return {"decision": "approve", "context": context}
    if kind != "complete":
        raise ValueError("This request needs an answer or rejection")
    if variant == "continue":
        answer = result.get("user_input", result.get("answer"))
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 10000:
            raise ValueError("Enter a non-empty response up to 10000 characters")
        return {"decision": "complete", "user_input": answer, "context": context}
    options = request["choices"]
    index = result.get("selected_index")
    if index is None:  # Compatibility with already-rendered label-only cards.
        matches = [
            i for i, c in enumerate(options) if c["label"] == result.get("answer")
        ]
        index = matches[0] if len(matches) == 1 else None
    if type(index) is not int or not 0 <= index < len(options):
        raise ValueError("Select one of the displayed choices")
    return {
        "decision": "complete",
        "index": index,
        **options[index],
        "context": context,
    }
