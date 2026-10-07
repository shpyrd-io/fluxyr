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

A script may `request_human('Which city?')`. Its first invocation exits in a waiting state. After the response is stored, the script starts from the beginning with `approval_response` populated, and `request_human` returns that value. Therefore request input **before** performing any side effect. Persist your own checkpoints in `data_dir` for multi-stage workflows. Tests use the same durable input flow.

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

## Approval API

Read `/api/jobs/{job_id}` for pending calls. Resolve each `call_id` with:

```text
POST /api/jobs/{job_id}/decisions/{call_id}
{"decision":"complete","result":{"answer":"New York"}}
```

Preflight approval uses `approve`; rejection uses `reject` plus `reason`. Repeating the same decision is idempotent. A conflicting second decision is rejected. All pending human requests in the batch must resolve before the job resumes. The interface uses this same API; a future internal or external approval channel can use it without changing the harness.
