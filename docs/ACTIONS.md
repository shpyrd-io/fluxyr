# Action and vault contracts

Action names may be short identifiers or display names (e.g. `Clima - Previsão`).
The backend normalizes them and returns the actual `function_name`, such as
`action_clima_previsao`. Use that result, not a guessed name. Creation saves a
candidate version; activation publishes a tested version. Activation is not a
user permission grant and a passing sample does not prove universal correctness.

The model-facing tools also accept `parameters_json` (create_action), `params_json`
(test_action), and `output_json`
(finish_execution). Each is a string containing JSON, mutually exclusive with its
ordinary object/array field. The registry parses it once and validates the same
canonical payload before dispatch. There is no implicit string-to-number/boolean
coercion. Example: params_json containing `{"count":4,"enabled":false}` preserves
an integer and boolean; `{"count":"4"}` remains a string and fails an integer
schema. This is an explicit transport option, not an XML parser.

## Complete local action

Skill: `local-reports`. Action: `action_write_report`.

Parameters:

```json
{"type":"object","properties":{"city":{"type":"string"}},"required":["city"],"additionalProperties":false}
```

Source (no dependencies):

```python
from fluxyr import params, data_dir, log, output
import json

report = {'city': params['city'], 'status': 'prepared'}
path = data_dir / 'report.json'
path.write_text(json.dumps(report, indent=2))
log(f'Report written to {path.name}')
output(report)
```

`output(value)` must be called exactly once. JSON output, stdout logs and exit code determine whether the action test passed. An exception/nonzero exit/missing output fails the test. Testing confirms the supplied example executed successfully, not universal correctness. Acceptance checks belong in the specification and generated code; the workbench must also compare actual test output with the user’s requirements. The handler supplies done/success/error automatically, without a model verdict.

An action can pause and resume up to five times using `request_input`, `request_choice`, or `request_confirmation` from `fluxyr`. Each interaction has a distinct stable `key`. Scripts restart from the top with an ordered, persisted reply history; prior helper calls return their own original replies, and the next unanswered helper suspends. The same version and initial parameters remain pinned. Small values carried through `context` and original choice candidates survive the pause. Replaying Python statements still requires idempotency: put effects after the final pause or use explicit persistent checkpoints.

Input returns `{decision: "complete", user_input, context}`; selection returns `{decision: "complete", index, label, context}` plus the original candidate's value/content/path; confirmation returns `{decision: "approve", context}`. Rejection returns `{decision: "rejected", reason, context}`. The legacy `request_human` returns `{answer}` for free text.

Choices contain 2–6 labels or objects. A candidate can include `preview_type` (html/image/text/file), `path` relative to data_dir, `content` for text or small HTML, and `description`. Inline HTML is saved as a local file before the card is shown. The selected candidate is taken from the stored request, never reinterpreted by a model. Choice cards have no free-text input; input cards have no options. Preflight approval remains separate from an action's internal confirmation.

The API decision URL uses `pending._request_id || pending.call_id`. Subsequent rounds get fresh request IDs while keeping the original tool call ID for the provider result. Stale duplicate submissions never answer a newer round. Selections submit `result: {selected_index: 0}` (legacy unambiguous answer labels still work); text submits `result: {answer: "..."}`. Invalid/blank answers are rejected before any job is queued.

Actions that set `requires_approval: true` park before dispatch. Declining that preflight never runs the script. Dependencies use package-index requirements such as `requests==2.32.3`; URL/path requirements are rejected. Pip installs run locally with a bounded timeout.

## Vault content formats

Create/edit through Vault or `POST /api/vault` with `{ "name": "...", "kind": "...", "content": {...} }`. Metadata reads omit all secret values. The `secret(name)` helper returns the content dictionary for names explicitly listed in an action's `secrets` array.

| Kind | Content fields |
|---|---|
| `text` | `value` |
| `key_password` | `key`, `password` (arbitrary additional fields allowed) |
| `access_token` | `access_token`, optional `token_type`, `expires_at` |
| `oauth2` | `client_id`, `client_secret`, `authorization_url`, `token_url`, optional `scope`, `grant_type`, `token_auth_method`, `certificate_name` |
| `certificate_pem` | `certificate`, `private_key`, optional `passphrase` |
| `certificate_pfx` | `pfx_base64`, optional `passphrase` |

OAuth `grant_type` may be `client_credentials` or `authorization_code`; refresh is automatic when a refresh token exists. `token_auth_method` accepts `client_secret_post` (default) or `client_secret_basic`. Click **Connect OAuth** for the authorization code flow. Configure the provider's redirect URI to `/api/vault/oauth/callback` on the installation's reachable address.

The workbench can call `check_vault_credential(name)` before building an integration. It actually resolves the credential, including an OAuth token exchange with the configured mTLS certificate when needed, and returns readiness without exposing the token or private content. This does not check resource API permissions. `vault_list` exposes safe flow/certificate selectors, the token endpoint hostname, and whether browser authorization is needed. Client credentials requires no browser connection.

Runner results identify `phase` and `executed`: a `credential_resolution` failure with `executed=false` happened before Python and should be fixed in the private Vault form, not by rebuilding code. Static validation rejects unknown Fluxyr helpers and invalid call signatures before accepting a candidate. In particular, `secret(name)` is a local lookup of resolved credentials and does not accept `force_refresh`.

Model-facing `list_skills`, `get_skill`, `list_executions` and `inspect_execution` return compact views. Request source explicitly with `get_skill(include_source=true, version_id=..., offset=...)`; follow `source_next_offset` for additional pages. Execution inspection returns recent significant events (not token fragments), with `next_before_event_id` for older pages. Full history and source remain available in persistence and the UI. Oversized legacy inspection results are shortened on conversation restore without changing user messages, action results or event history.

Validation: `FLUXYR_TEST_LIVE_MTLS=1 .venv/bin/pytest -q -s tests/test_live_mtls_integration.py` runs an opt-in MiniMax M3 integration build against a local HTTPS API requiring a client certificate. The test uses synthetic OAuth credentials, checks actual HTTP calls and generated Python, and asserts that secrets never enter provider requests. The successful 2026-10-07 probe generated one candidate, tested and activated it in 14 model calls. It does not access Banco Inter or validate real account permissions.

For API calls, `secret('oauth-name')` resolves/refreshes tokens before the action starts. Use its `access_token` in the documented authorization header. A declared certificate secret can be written into private files within the invocation directory and passed to the HTTP client. OAuth token requests can refer to a vault certificate with `certificate_name` for native mTLS; PEM and PFX are converted to temporary PEM material and cleaned up after the request.

## Routine execution

```json
{
  "name": "Morning forecast",
  "prompt": "Call action_forecast for New York, log the forecast and finish_execution with the resulting JSON and evidence.",
  "cron": "0 8 * * *",
  "timezone": "UTC",
  "enabled": true,
  "overlap": "queue"
}
```

This definition expects an existing, tested `action_forecast`. Scheduling it does not create that action or supply credentials.

Concurrency is configured on the routine and enforced when workers start a job:

- `overlap: "queue"` (default): one processing execution at a time; further runs wait.
- `overlap: "skip"`: skip scheduled occurrences when the routine already has queued or active work. Explicit manual runs and accepted webhooks are preserved in the queue.
- `overlap: "parallel", max_concurrency: 3`: run up to three executions of this routine simultaneously; excess runs remain queued. `max_concurrency` accepts integers from 1 to 32 and defaults to 1.

The global `FLUXYR_WORKERS` limit (default 4) still bounds all execution workers together. Messages in the same session always run sequentially, even in parallel mode. A job suspended for human input releases its processing slot, but keeps its own session blocked; after the answer, it queues for capacity ahead of later messages in that session. Lowering a routine's limit never interrupts running work: new starts wait until occupancy drops below the new limit. Form changes take effect only after **Save**.

Schema version 6 adds `max_concurrency`; existing routines retain their policy with a limit of 1. In particular, existing reactive routines using `queue` now serialize across sessions too. Choose `parallel` and a limit to enable concurrent customer conversations. Older engine versions cannot open a database after this migration.

## Approval API

Read `/api/jobs/{job_id}` for pending calls. Resolve each `call_id` with:

```text
POST /api/jobs/{job_id}/decisions/{call_id}
{"decision":"complete","result":{"answer":"New York"}}
```

Preflight approval uses `approve`; rejection uses `reject` plus `reason`. Repeating the same decision is idempotent. A conflicting second decision is rejected. All pending human requests in the batch must resolve before the job resumes. The interface uses this same API; a future internal or external approval channel can use it without changing the harness.

### Parallel calls and executed versions

Independent published `action_*` calls emitted in the same model turn can run in parallel (including multiple calls to the same action). The pool is bounded by `FLUXYR_TOOL_WORKERS` (default 6); with one worker the persisted batch is sequential. Batches above eight calls retain sequential dispatch and its human-wait cap. Each invocation has its own directory and effect occurrence; `data_dir` and external resources remain shared, so dependent operations must be issued in separate turns. Candidate tests, activation and skill edits remain sequential.

Human waits in concurrent batches retain completed siblings and independent pending cards. Resuming a parked action does not replay completed siblings. Job snapshots pin action code; a job's explicit activation can update its selected action. Runner results include `version_id` and `source_sha256`; tool begin/end events also carry the pinned version. These fields, rather than model explanations or log wording, identify the code used.

Validation (2026-10-07): real MiniMax M3 / OpenRouter, job `c7822d86-2dc7-47c9-9f4e-798f85fb2f66`, repeated the user's two-2d20 request in an isolated schema. Both calls began before either completed (~81 ms overlap), using version `86da66b8-948e-46a9-aaaa-a0054d4f3959`. `scripts/validate_live_parallel.py --run --serve` reproduces the probe using the existing dice action, copied read-only into an isolated instance. Separate executor tests use actual Python subprocesses with a cross-process rendezvous, concurrent activation, one completed/one parked sibling, two independent human responses, and the single-worker fallback. The executor tests use a scripted model only to select dispatch calls; the MiniMax probe does not.
