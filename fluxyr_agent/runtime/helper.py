"""Copied into action environments as `fluxyr`. No backend imports or DB access."""

import json
import os
import sys
from pathlib import Path

_payload = json.loads(sys.stdin.read())
params = _payload.get("params", {})
data_dir = Path(os.environ["FLUXYR_DATA"])
approval_response = _payload.get("approval_response")


def secret(name):
    if name not in _payload.get("secrets", {}):
        raise KeyError(f"Secret {name!r} was not declared by this action")
    return _payload["secrets"][name]


def output(value):
    print("__FLUXYR_RESULT__" + json.dumps(value), flush=True)


def log(value):
    print(str(value), flush=True)


def request_human(question):
    if approval_response is not None:
        return approval_response
    print("__FLUXYR_WAIT__" + json.dumps({"question": str(question)}), flush=True)
    sys.exit(75)
