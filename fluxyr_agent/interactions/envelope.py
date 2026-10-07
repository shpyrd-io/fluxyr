"""Canonical PUA (Pending User Action) envelope builders and validators."""

# 'choices' and 'input' are reserved framework extension seams — no backend emitter yet.
_VALID_PUA_TYPES = {"approval", "choices", "input", "filespace_required"}
_VALID_APPROVAL_VARIANTS = {"confirm-reject", "choices", "continue"}
_PUA_TITLE_MAX = 100
_PUA_MESSAGE_MAX = 500
_PUA_VERSION = 1


def build_approval_pua(
    variant: str, title: str, message: str | None = None, **extra
) -> dict:
    """Build a ``__pua__`` dict for type='approval'.

    Args:
        variant: One of 'confirm-reject', 'choices', 'continue'.
        title: Short human-readable title (max 100 chars).
        message: Optional descriptive message (max 500 chars).
        **extra: Additional fields merged into the payload.

    Returns:
        A fully-formed ``__pua__`` dict.
    """
    payload: dict = {"variant": variant, **extra}
    pua: dict = {
        "version": _PUA_VERSION,
        "type": "approval",
        "title": title,
        "channels": ["chat"],
        "payload": payload,
    }
    if message is not None:
        pua["message"] = message
    return pua


def build_filespace_required_pua(
    mode: str,
    pending_args: dict,
    arg_name: str | None = None,
    title: str | None = None,
    tool_name: str | None = None,
    mount_point: str | None = None,
) -> dict:
    """Build a ``__pua__`` dict for type='filespace_required'.

    Args:
        mode: 'mount' (grant the chosen filespace to this scope, then retry)
            or 'arg' (inject the chosen filespace id as ``arg_name``).
        pending_args: Original args dict to replay after filespace selection.
            Stored verbatim: every mode replays the parked call with these
            arguments, so truncation would corrupt the replay.
        arg_name: Required when mode='arg' — the arg key that receives the filespace id.
        title: Optional title override (defaults to 'Select a Workspace').
        tool_name: Tool that initiated the request.

    Returns:
        A fully-formed ``__pua__`` dict.
    """
    # Args are stored VERBATIM for every mode. The resume path replays the
    # parked tool call with exactly these arguments, so truncating them would
    # silently destroy the payload — a filespace_write carrying 60 KB of
    # content would come back as {'__truncated__': True} and write nothing.
    # Write content is already bounded by the tool schema, so the envelope
    # cannot grow without bound.
    stored_args = pending_args
    payload: dict = {
        "mode": mode,
        "pending_args": stored_args,
    }
    if arg_name is not None:
        payload["arg_name"] = arg_name
    if tool_name is not None:
        payload["tool_name"] = tool_name
    if mount_point is not None:
        # The mount point the model tried to use, so the picker can offer to
        # mount a filespace there rather than asking an open-ended question.
        payload["mount_point"] = mount_point
    return {
        "version": _PUA_VERSION,
        "type": "filespace_required",
        "title": title or "Select a Workspace",
        "channels": ["chat"],
        "payload": payload,
    }


def validate_pua(pua: dict) -> bool:
    """Validate a ``__pua__`` envelope structure.

    Returns True if the envelope is structurally valid, False otherwise.
    """
    if not isinstance(pua, dict):
        return False

    pua_type = pua.get("type")
    if pua_type not in _VALID_PUA_TYPES:
        return False

    title = pua.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    if len(title) > _PUA_TITLE_MAX:
        return False

    message = pua.get("message")
    if message is not None:
        if not isinstance(message, str):
            return False
        if len(message) > _PUA_MESSAGE_MAX:
            return False

    channels = pua.get("channels")
    if not isinstance(channels, list) or not channels:
        return False

    if pua_type == "approval":
        payload = pua.get("payload")
        if not isinstance(payload, dict):
            return False
        variant = payload.get("variant")
        if variant not in _VALID_APPROVAL_VARIANTS:
            return False

    return True


def extract_pua(output: dict) -> "tuple[dict | None, dict]":
    """Extract ``__pua__`` from an output dict.

    Returns:
        ``(pua, context)`` where context is the output dict minus ``__pua__``.
        Returns ``(None, output)`` if no ``__pua__`` key is present.
    """
    if not isinstance(output, dict) or "__pua__" not in output:
        return None, output
    pua = output["__pua__"]
    context = {k: v for k, v in output.items() if k != "__pua__"}
    return pua, context
