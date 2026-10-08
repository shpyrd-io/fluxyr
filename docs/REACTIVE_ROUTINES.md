# Reactive routines

A reactive routine receives webhooks, normalizes provider payloads with Python,
and queues agent executions in persistent conversations. No model runs to route
an incoming webhook.

## Collect first

Ask the agent to create a reactive routine in collection mode. In **Routines**,
choose **Reactive · webhook** when creating a routine. Its inbox shows a POST URL:

```text
POST /api/webhooks/<private-token>
```

Configure the provider with that path on your public deployment URL. Keep the
URL private: its random token authorizes delivery. The receiving route must be
reachable through your deployment's proxy/authentication layer. It is separate
from approval API authentication.

The receiver persists the request and returns **202** with `receipt_id`, `status`
and `duplicate`. This acknowledges durable receipt, not successful agent work.
A disabled routine returns 409; an unknown URL returns 404.

Modes:

- **collecting**: save examples without running code, a model, or creating sessions.
- **active**: run the pinned normalizer and enqueue its accepted events.
- **disabled**: reject new receipts and hold pending normalization work.

Changing settings only takes effect when saved. Activating never dispatches old
collected examples. Explicit replay can trigger real actions; select samples
carefully.

## Normalizer contract

The versioned normalizer is a technical Python script, not a skill tool exposed
to the model. It uses the existing Python runner, environment cache and optional
Landlock protection. A normalization attempt supplies its workspace identity;
no conversation or agent job is needed to execute it.

`from fluxyr import params, output` provides:

| Input | Value |
| --- | --- |
| `params['json']` | Parsed JSON value, or `None` for other bodies |
| `params['form']` | URL-encoded form fields; every value is a list of strings |
| `params['text']` | UTF-8 body for other content types, otherwise `None` |
| `params['query']` | URL query parameters, with lists preserving repeated values |
| `params['content_type']` | Request MIME type |

Binary uploads and multipart bodies are not supported by the generic receiver.
Use file references or a custom authenticated route. Requests and parsed
inputs are each limited to 256 KiB. Credentials should not be put in samples.

Example for a fictitious provider with two message variants and batched delivery:

```python
from fluxyr import params, output

body = params['json']
items = body if isinstance(body, list) else [body]
events = []
for item in items:
    if item['type'] in ('presence', 'read'):
        continue
    if item['type'] == 'message':
        sender, text = item['sender'], item['text']
    elif item['type'] == 'button_reply':
        sender, text = item['reply']['sender'], item['reply']['label']
    else:
        raise ValueError('Unsupported provider event type')
    events.append({
        'session_key': f"{item['account']}:{sender}",
        'event_id': f"{item['account']}:{item['id']}",
        'payload': {'sender': sender, 'text': text},
    })
output({'events': events} if events else {
    'events': [], 'ignored_reason': 'Presence/read updates',
})
```

Adapt the code to actual samples or provider documentation. A field's identity
can depend on the event type: do not indiscriminately use `A or B` where A could
be a recipient and B a sender. Extract fields within the same array item.

Output requirements:

- `events`: at most 100 entries, at most 256 KiB total output.
- Each entry: nonempty `session_key` (up to 255 characters), `payload` containing
  JSON-compatible data, optional nonempty `event_id` (up to 255 characters).
- An empty array requires `ignored_reason` (up to 500 characters).
- Missing identity for an accepted message must raise an error. There is no
  automatic shared `default` conversation.

Normalizers must be pure transformations: no network requests, Vault lookups,
file changes, human pauses or outgoing messages. Put business effects in the
subsequent agent actions. Dry-run blocks downstream dispatch; arbitrary Python
side effects are not rolled back. This uses the installation's trusted-code
execution model, not a new security sandbox.

The total normalization deadline is 15 seconds, including dependency preparation;
prefer standard-library code. Environments are cached per version. Attempts save
version, timestamps, output/error and dry-run status. Collection does not create
an environment.

## Test, activate and inspect

Save a normalizer version, select a collected receipt and **Test selected version**.
Check the emitted session keys and payloads, not only Python success. Test all
representative formats, batch inputs, ignored events and missing identity.
Activation requires a successful dry run of that exact version. This is a minimum
gate, not proof that every provider event variant has been covered.

The agent uses `list_routine_receipts`, `inspect_routine_receipt`,
`create_routine_normalizer`, `test_routine_normalizer`,
`configure_reactive_routine`, `inspect_routine_normalizer` and `replay_routine_receipt` for the same workflow.
Inspection is paginated to avoid placing the entire inbox in model context.

Each accepted event produces a job in the session identified by
`(routine_id, session_key)`. Jobs in one session run sequentially and share
context; a pending human decision holds later jobs. Other sessions can execute
concurrently within the engine's worker limit. Each job completes independently;
no worker stays occupied waiting for the next webhook.

Receipts show `collected`, `pending`, `processing`, `ignored`, `routed` or `failed`.
`routed` means jobs were queued; inspect linked execution statuses for their
actual outcome. Pending receipts retain the normalizer version and routine
instructions selected when they were received. Disabling/changing to collection
during normalization prevents downstream dispatch; explicit replay is needed.

## Retries, retention and restart

Send `Idempotency-Key` (1–255 characters) to deduplicate repeated HTTP deliveries
while the receipt exists. A duplicate returns the original receipt ID.
Normalizer `event_id` deduplicates provider messages across different receipts
within the same routine for 30 days. Without it, only the same receipt/item can
be deduplicated. This does not promise exactly-once external side effects.

The UI shows 20 receipt summaries per page. Older pages replace the current page;
rows never accumulate in the DOM. Payloads and attempts load only when opened.
There is no continuous stream of received payloads to the browser.

Collected and finished receipts/attempts expire after seven days or earlier when
the 1,000-receipt retention cap is reached: oldest finished samples rotate in
batches. Pending/running receipts are never pruned. If all retained receipts are
pending or being tested, new deliveries return 429 for the provider to retry.
Execution history is preserved, and successful version-test evidence survives
sample rotation. External event-ID deduplication remains separate from samples.

Pending receipts survive server restart. Interrupted normalization becomes a
visible failure for explicit replay; Python or downstream side effects are not
blindly repeated. Failed and ignored receipts may be replayed with the currently
active tested version. Successfully routed receipts cannot be replayed through
this endpoint.

## Webhook authentication and custom routes

The inbox supports optional HMAC-SHA256 configured through its private settings:
set a signing secret of at least 16 characters. Send the hex HMAC of the exact raw
request body in `X-Webhook-Signature` (optionally prefixed with `sha256=`).
The secret is stored encrypted and is not returned in routine metadata. It is
independent of `FLUXYR_APPROVALS_API_KEY`. Configure an empty signing secret via
PATCH to remove optional HMAC verification.

Providers with different signature conventions or verification handshakes can
use an application route and deliver to the same inbox after validation:

```python
from flask import request
from fluxyr import Fluxyr

app = Fluxyr(__name__)

@app.post('/integrations/provider')
def provider_event():
    # Validate this provider's signature over request.get_data() here.
    # Reject invalid requests before calling receive_event.
    raise NotImplementedError('Implement provider authentication first')
    return app.receive_event(
        'routine-id',
        {'json': request.get_json(), 'form': {}, 'text': None,
         'query': request.args.to_dict(flat=False),
         'content_type': request.mimetype},
        idempotency_key=request.headers.get('Idempotency-Key'),
    ), 202
```

`app.receive_event` is a trusted in-process API, not an authentication layer.
Start the app's existing worker (`app.run()` or `app.start()`) to process receipts;
no separate service or broker is required.

## Management API

| Method and path | Purpose |
| --- | --- |
| `POST /api/routines` with `trigger: "reactive"` | Create a collecting sink |
| `PATCH /api/routines/<id>/reactive` | Set mode, normalizer_id or signing_secret |
| `GET /api/routines/<id>/receipts?before=&limit=` | Paginated receipt summaries |
| `GET /api/routines/<id>/receipts/<receipt>` | Payload, attempts and linked executions |
| `DELETE /api/routines/<id>/receipts/<receipt>` | Delete an inactive receipt |
| `POST /api/routines/<id>/receipts/<receipt>/replay` | Explicitly enqueue an eligible receipt |
| `GET /api/routines/<id>/normalizers` | Saved normalizer version summaries |
| `GET /api/routines/<id>/normalizers/<version>` | Source and metadata |
| `GET /api/routines/<id>/sessions?after=` | Paginated conversation identities |
| `POST /api/routines/<id>/sessions/reset` | Reset idle identity with `session_key`; preserve history |
| `POST /api/routines/<id>/normalizers` | Save immutable source/dependencies |
| `POST /api/routines/<id>/normalizers/<version>/test` | Dry-run with `receipt_id` |

Management endpoints use the same deployment access layer as other application
APIs. Only the webhook receiver uses the routine URL token/HMAC.
