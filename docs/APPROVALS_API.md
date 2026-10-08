# Approval API

A human interaction parks its job in `waiting`. Submit the response to the
current request ID, and Fluxyr queues the job after all pending responses arrive.
An HTTP 200 records a decision; it does not mean the action has finished.

## Optional authorization

```dotenv
FLUXYR_APPROVALS_API_KEY=your-random-secret
```

Restart after changing the environment. An unset or empty value means **no
authentication**. A nonempty value requires the following header:

```http
Authorization: Bearer your-random-secret
```

This protects only:

- `GET /api/approvals` (including HEAD).
- `GET /api/approvals/{job_id}/{request_id}/assets/{choice_index}` (including HEAD).
- `POST /api/approvals/{job_id}/{request_id}/decision`.
- `POST /api/approvals/{job_id}/{request_id}/credential`.
- `POST /api/jobs/{job_id}/decisions/{request_id}`.
- `POST /api/jobs/{job_id}/vault/{request_id}`.

Missing or invalid authorization returns HTTP 401 with
`{"error":"Approval API key required or invalid","code":"approval_auth_required"}`
and a `WWW-Authenticate: Bearer` header. Tokens in URLs or JSON bodies are not
accepted. The shared key grants access to all approvals; there are no per-user
permissions or identities. Use HTTPS when calling across a network.

The UI asks for the key when submitting a protected response. It keeps the key
only in the current tab's memory until reload, and sends it only in the header
of approval requests. It is not stored in browser storage, model context or
decision payloads. Cancelling the key dialog leaves the request unanswered.

**This is not authentication for the whole installation.** Jobs, sessions, events,
files, direct Vault management and other APIs remain open. Existing job/session
responses still contain `pending`, and events may contain the interaction cards.
This option controls submission of human responses and access to the dedicated
approval list; it does not make conversation data confidential or protect other
instance operations.

## List open requests

```sh
curl -H "Authorization: Bearer $FLUXYR_APPROVALS_API_KEY" \
  http://127.0.0.1:5050/api/approvals
```

Returns a JSON array of currently unanswered, parked human/Vault requests from
waiting jobs, oldest jobs first. Child executions appear with their own job IDs.
It excludes waits for builders/other jobs and requests already answered.

```json
[
  {
    "job_id": "job-uuid",
    "session_id": "session-uuid",
    "request_id": "current-request-id",
    "call_id": "original-provider-call-id",
    "tool_name": "ask_human",
    "kind": "human",
    "request": {
      "type": "approval",
      "title": "Which environment?",
      "payload": {
        "variant": "choices",
        "choices": ["Production", "Sandbox"]
      }
    },
    "assets": [],
    "decision_url": "/api/approvals/job-uuid/current-request-id/decision",
    "submission_url": "/api/approvals/job-uuid/current-request-id/decision"
  }
]
```

`request` is the persisted UI card. Read `request.payload.variant`:
`confirm-reject`, `choices`, or `continue` (free text).
`request.payload.preflight=true` also means confirmation before execution.
Choices may be labels or objects with `label` and optional preview metadata.

For existing clients using `GET /api/jobs` (latest 200 jobs) or
`GET /api/jobs/{id}`, inspect jobs with `status=waiting` and `pending` entries
with `_status=parked`, no `_decision`, and `_result.__pua__`. Their current request
ID is `_request_id || call_id`. Prefer `/api/approvals` for new integrations.

## Submit a decision

POST JSON to the returned `decision_url`, with the Bearer header when enabled:

| Interaction | JSON body |
| --- | --- |
| Approve a confirmation | `{"decision":"approve"}` |
| Reject | `{"decision":"reject","reason":"Optional reason"}` |
| Select first choice (zero-based index) | `{"decision":"complete","result":{"selected_index":0}}` |
| Answer free text | `{"decision":"complete","result":{"user_input":"My response"}}` |

```sh
curl -X POST \
  -H "Authorization: Bearer $FLUXYR_APPROVALS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"decision":"complete","result":{"selected_index":0}}' \
  http://127.0.0.1:5050/api/approvals/JOB_ID/REQUEST_ID/decision
```

Success returns `{"status":"queued","remaining":0}`, or `waiting` with a
positive `remaining` count when other responses are outstanding. An identical
repeated submission returns `{"duplicate":true,"status":"..."}` without
executing twice. A different response to an already answered request, an invalid
choice, a cancelled job or a stale request returns HTTP 400.

Each new interaction round can have a new `request_id` while retaining the
original `call_id`. Always reload the current request; do not reuse the old ID
for a later question. After responding, inspect `GET /api/jobs/{job_id}` or
subscribe to `GET /api/events?session_id=...` for execution progress.

## Requested Vault credentials

For `kind=vault_credential`, the public card describes the type, suggested name,
create/edit mode and public OAuth settings. Save private values by POSTing to
the returned `submission_url` (`/api/approvals/{job_id}/{request_id}/credential`):

```json
{
  "name": "Example credential (Production)",
  "kind": "key_password",
  "content": {"key": "private-key-value", "password": "private-password-value"}
}
```

The credential type must match the request; its content fields depend on the
[Vault type](ACTIONS.md). Fluxyr encrypts the item and records only its metadata
as the decision. Do not send credentials through generic `result` or `reason`.
Generic approval cannot save a Vault item. To decline a credential request,
POST `{"decision":"reject"}` to `decision_url`.

## Preview assets and an external authentication layer

Choices created with `request_choice` can include `preview_type`, `path`, `content`
and a label. Inline HTML is materialized in the data directory by the runner.
The approval list returns explicit `assets` for file-backed choices, for example:

```json
{
  "choice_index": 0,
  "label": "Blue card",
  "preview_type": "html",
  "mime_type": "text/html",
  "url": "/api/approvals/JOB_ID/REQUEST_ID/assets/0"
}
```

The corresponding choice's `url` in `request.payload.choices` points to the same
protected asset route. Text previews remain inline in `content`. Standalone
`render_preview` messages and files not explicitly attached to a choice are not
automatically part of an approval; simple confirmations have no assets.

Fetch each asset using the same Bearer header. The response contains the original
file bytes and MIME type, supports conditional/range requests, and uses no-store
and sandbox headers. Only the saved choice's file inside `data/` is accessible;
the caller cannot supply an arbitrary file path. Answered, cancelled, or superseded
requests return 404 for their assets. A missing file also returns 404.

```sh
curl -H "Authorization: Bearer $FLUXYR_APPROVALS_API_KEY" \
  http://127.0.0.1:5050/api/approvals/JOB_ID/REQUEST_ID/assets/0 \
  --output preview.html
```

Browser `<img>` and `<iframe>` elements cannot attach a custom Bearer header.
An integrating application should fetch the bytes through its authenticated
backend, or fetch with the header and create a local Blob URL. Render HTML in a
sandboxed iframe, never inject it into the application's DOM. Use self-contained
HTML with inline/data assets: this endpoint serves the attached file, not an
arbitrary directory of CSS, scripts or images. Relative dependencies in HTML are
not automatically packaged or exposed.

If a reverse proxy/SSO layer protects the entire installation, the Fluxyr key
cannot bypass that upstream layer. With `FLUXYR_APPROVALS_API_KEY` configured,
route **only `/api/approvals` and `/api/approvals/*`** through to Fluxyr without
the interactive login, forwarding the `Authorization` header. Keep the rest of
the application behind the existing layer. Alternatively authenticate against
both layers. Dedicated listing, decisions, credential submissions and assets all
use this prefix; integrations do not need to bypass `/preview` or general file APIs.
Never exempt this prefix upstream while leaving the Fluxyr key empty unless
unauthenticated approvals are intended.

## Private browser input

With browser tools enabled, `kind=browser_private_input` identifies a private
browser field request. Follow `submission_url` and POST `{"value":"..."}` for a
temporary value, or `{}` when `request.payload.vault_item_id` is present to authorize
that saved item. Values never go to `decision_url`, chat or ordinary `ask_human`.
Only safe delivery outcomes enter execution history. Both the approvals route
and the `/api/jobs/{id}/private-input/{request_id}` alias require the configured
approval Bearer key. See [browser setup and lifecycle](BROWSER.md).
