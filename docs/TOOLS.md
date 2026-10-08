# Native tools

Fluxyr ships with **45 built-in tools** and **5 optional browser tools** across the agent and the isolated skill
builder. They cover research, files, skills, credentials, human interaction,
routines, execution inspection and memory. This reference follows the current source.

Call `list_tools` in an agent conversation to inspect the actual available names,
descriptions and JSON input schemas, including active skill actions and tools
registered by the application.

In the tables below, `?` marks an optional parameter. **Both** means the tool is
available to the agent and the skill builder; **Agent** and **Builder** identify
tools restricted to those contexts. Memory tools are added to both contexts by
the brain. The automatic memory extractor has a separate internal catalogue,
listed at the end.

## Discovery and research

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `list_tools` | Agent | None | List the tools available in the current context with their input schemas. Does not execute them. |
| `get_current_datetime` | Both | `timezone?` | Read the current date, time, weekday, UTC offset and Unix timestamp from the server clock. Accepts an IANA timezone such as `America/Sao_Paulo` or `UTC`; defaults to the server's local timezone. |
| `web_browse` | Both | `url`, web options below | Fetch a public HTTP(S) page and return its content, normally as clean Markdown. |
| `web_extract` | Both | `url`, `css_selector`, web options below | Extract a section using a simple tag, class or ID selector, such as `article`, `.content` or `#main`. |
| `tech_doc` | Both | `url`, `endpoints?` | Extract and distill API documentation into an integration reference, optionally focused on a list of endpoints. |

Both web tools accept `format?` (`markdown`, `text`, `raw`), `include_links?`,
`include_images?`, `max_chars?`, `timeout?`, `headers?` and `user_agent?`.
Defaults are Markdown, links and images included, 50,000 returned characters and
a 30-second request timeout. They fetch HTTP content without executing browser
JavaScript. For authenticated integrations, generated actions can access Vault
credentials without putting secret values into model tool arguments.

## Files and shell

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `read` | Both | `path`, `offset?`, `limit?` | Read text or an image. Text responses are bounded to 2,000 lines or 50 KiB; `offset` is one-based. |
| `write` | Both | `path`, `content` | Create or overwrite a UTF-8 file, creating parent directories as needed. |
| `edit` | Both | `path`, `edits` | Apply exact text replacements and return a diff. Each entry has `oldText` and `newText`; matches must be unique and non-overlapping in the original file. |
| `bash` | Both | `command`, `timeout?` | Execute Bash commands, stream stdout/stderr and return bounded output, with a full-output file when truncated. |
| `render_preview` | Agent | `path`, `title?` | Display a local file in the conversation. It does not pause execution or collect a choice. |

Relative file paths and Bash's initial working directory use the persistent data
root, shared with Python actions through `data_dir`. The four file/shell tools
also accept absolute paths and `~`. Each Bash invocation starts a fresh shell;
`cd` and `export` do not persist to the next call. Its default and maximum timeout
come from `FLUXYR_TOOL_TIMEOUT`.

Preview files must be inside the data root. A Python action's temporary working
directory is separate from this persistent directory. See
[execution protection](EXECUTION_PROTECTION.md) for deployment write restrictions.

## Skills and Python actions

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `list_skills` | Both | None | List skills, action metadata and version test summaries. |
| `get_skill` | Both | `skill_id`, `include_source?`, `version_id?`, `offset?` | Inspect one skill's instructions, specification and test summaries; optionally read generated source, with pagination. |
| `create_skill` | Agent | `name`, `description`, `instruction`, `spec` | Save skill metadata and a Markdown technical specification. Returns the UUID used by subsequent calls; does not generate Python. |
| `update_skill` | Agent | `skill_id`, `name?`, `description?`, `instruction?`, `spec?` | Update metadata or the specification. Changing the specification does not itself replace action code. |
| `set_skill_enabled` | Agent | `skill_id`, `enabled` | Enable or disable a skill and its actions while preserving code and history. |
| `delete_skill` | Agent | `skill_id` | Remove a skill from the available catalogue while preserving execution history. |
| `build_skill` | Agent | `skill_id` | Dispatch the isolated builder for a saved specification. The parent conversation waits durably and resumes with the candidate versions. |
| `rebuild_action` | Agent | `skill_id`, `action_id` | Rebuild one action from the updated specification, preserving the other actions and existing active versions. |
| `submit_plan` | Builder | `skill_id`, `actions` | Save the ordered build plan before code generation. Each action has a `name` and `description`. |
| `create_action` | Builder | `skill_id`, `name`, `description`, `source`, `parameters` or `parameters_json`; `dependencies?`, `secrets?`, `requires_approval?` | Save an immutable Python candidate with its input JSON Schema, pip requirements, dynamic `x-vault` selectors or legacy fixed Vault names. |
| `test_action` | Agent | `version_id`, `params` or `params_json` | Execute a candidate with real inputs and return the execution result, errors and logs. Tests can have real effects. |
| `activate_action` | Agent | `version_id` | Publish a version with a passing test so its callable action becomes available. |
| `update_action_description` | Agent | `action_id`, `description` | Improve an action's description using observed behavior, without changing its Python code. |

The usual flow is `create_skill` → `build_skill` → inspect → `test_action` →
`activate_action`. The builder uses `submit_plan` and `create_action`; the agent
receives the resulting candidates for validation. Builds do not activate actions
automatically. A repair follows `update_skill` → `rebuild_action` → test → activate.

Use the actual IDs and callable names returned by these tools. Prefer dynamic
Vault parameters for actions that can run with different accounts or environments.

### Selecting credentials per action call

Mark a top-level string property in `parameters` (or `parameters_json`) with
`"x-vault": true`:

```json
{
  "type": "object",
  "properties": {
    "credential": {
      "type": "string",
      "x-vault": true,
      "description": "Vault item ID for the selected Control Tower bearer token"
    }
  },
  "required": ["credential"]
}
```

The agent finds the item using `vault_list` metadata (name, ID, type and safe
configuration), then calls the action with `{"credential": "<Vault item UUID>"}`.
It should ask the user when the intended account or environment is ambiguous.
The generated Python accesses the bound content dictionary:

```python
from fluxyr import params, secret

credential_id = params["credential"]  # Still only the selected ID.
token = secret("credential")["value"]  # For a text Vault item.
# Use token in the HTTP request; never output or log it.
```

Use `access_token` instead of `value` for OAuth/access-token items. OAuth refresh
and linked mTLS configuration continue to be handled by Vault. Passkeys are still
restricted to the browser authenticator.

Only selected items are resolved, privately, before Python starts. The caller's
parameters, model context and execution history contain IDs, not decrypted
contents. Existing output/log redaction also covers these resolved values.
Credentials are re-resolved privately when a human interaction resumes the same
call. New or renamed items can be selected without rebuilding the action.

Vault parameters must be top-level strings with no default. Use `required` for
mandatory selectors; omitting an optional selector creates no secret binding.
Do not use nested selectors, fixed ID enums, or the same parameter name as a
fixed secret. Do not call `secret(params["credential"])`: the helper accepts the
**parameter name**, not its ID.

Legacy `secrets` still contains exact existing Vault names, matching
`secret("same name")` in Python. All fixed entries are resolved on every call,
so never add nonexistent placeholders or a list of alternative environments.
To convert an existing fixed action, update its spec and create/test/activate
one version with an x-vault parameter; subsequent credential changes need no
rebuild. Continuous Worker listeners retain their separate fixed-name
`ctx.secret(name)` contract.
`requires_approval` controls preflight approval, separately from questions asked
inside an action. See [the action contract](ACTIONS.md).

For open-ended JSON, the `*_json` alternatives accept JSON encoded as a string.
Supply either the structured field or its JSON-text alternative, never both.

## Credentials and human interaction

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `vault_list` | Both | None | List credential IDs, names, types and safe OAuth configuration, including the flow and linked certificate. Never returns secret values. |
| `check_vault_credential` | Agent | `name` | Check readiness and return safe diagnostics. OAuth checks may obtain or refresh a token using configured mTLS; they do not call the resource API or test action code. |
| `manage_vault_credential` | Both | `action`, `vault_item_type?`, `suggested_name?`, `vault_item_id?`, `oauth_grant_type?`, `mtls_certificate_id?`, `oauth_config?` | Open a private embedded create/edit form and wait for save or cancellation. Prefill public OAuth settings for review; saved secret values do not enter the chat context. |
| `ask_human` | Both | `question`, `choices?` | Pause durably for an answer. Question: 1–500 characters. Omit choices for free text, or provide 2–6 non-empty string labels of at most 100 characters each. Put explanations in the question rather than long option labels. |

For `manage_vault_credential`, `action` is `create` or `edit`. Creation requires a
`vault_item_type`; editing requires an existing `vault_item_id` from `vault_list`.
Supported types are `text`, `key_password`, `oauth2`, `access_token`,
`certificate_pem` and `certificate_pfx`. OAuth can preselect `authorization_code`
or `client_credentials` and link an existing PEM/PFX credential for mTLS.

`oauth_config` accepts only public `token_url`, `authorization_url`, `scope` and
`token_auth_method` (`client_secret_post` or `client_secret_basic`). URLs accept
up to 2,048 characters and scopes up to 2,000. Prefills work for create and edit;
the user can change them and must save the form before anything is persisted.
Client IDs, secrets, tokens and certificate contents remain private form inputs.

```json
{
  "action": "create",
  "vault_item_type": "oauth2",
  "suggested_name": "weather_oauth",
  "oauth_grant_type": "client_credentials",
  "oauth_config": {
    "token_url": "https://api.example.com/oauth/token",
    "scope": "weather.read",
    "token_auth_method": "client_secret_post"
  }
}
```

Replace the example endpoint and scopes with those from the provider documentation.

Use `ask_human` for clarification while the agent works or builds a skill.
Interactions inside Python actions use the runtime helpers `request_input`,
`request_choice` and `request_confirmation`; those are Python APIs, not additional
model-facing tools. See [human interaction](HUMAN_INTERACTION.md).

## Routines and execution inspection

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `list_routines` | Agent | None | List saved routines and their schedules. |
| `create_routine` | Agent | `name`, `prompt`, `trigger?`, `cron?`, `timezone?`, `enabled?`, `overlap?`, `max_concurrency?` | Save a manual, scheduled, reactive or worker routine. `queue` (default) serializes runs; `skip` skips scheduled occurrences while busy; `parallel` permits up to `max_concurrency` (integer 1–32, default 1) and queues excess runs. Global worker capacity and session ordering still apply. |
| `update_routine` | Agent | `routine_id`, `values` | Update routine fields including `overlap` and `max_concurrency`. Lowering concurrency leaves running work intact and restricts subsequent starts. |
| `run_routine` | Agent | `routine_id` | Enqueue a manual run in its own conversation, linked to the calling execution. |
| `list_executions` | Agent | `limit?`, `before?` | List recent execution IDs and concise status. Limit is 1–50; use returned `next_before` to page backward. |
| `inspect_execution` | Both | `job_id`, `before_event_id?`, `limit?`, `include_logs?` | Read significant events, errors and messages without individual stream fragments. Limit is 1–30; page older events with returned `next_before_event_id`. |
| `finish_execution` | Agent | `evidence`, `output` or `output_json` | Record the task result and narrative evidence for inspection. It does not determine whether the execution succeeded; action execution results do. |

An enabled schedule requires a cron expression. Success checks belong in action
specifications and Python implementations, not in a separate routine expectation.
Routine deletion and execution pause/cancel are available through the interface
and HTTP API, rather than additional tools in this catalogue.

## Memory

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `read_memory` | Both | `memory_type?`, `query?`, `key?` | Read saved memories. Type is `all` (default), `semantic`, `episodic` or `implicit`; query is a case-insensitive text filter and key selects a semantic entry. Never writes. |
| `save_in_memory` | Both | `memory_type`, `content`, `key?`, `importance?`, `metadata?` | Save a semantic fact or episodic event. Importance ranges from 0 to 1, defaulting to 0.5; key identifies a semantic fact. |
| `delete_memory` | Both | `memory_type`, `key` | Delete a semantic entry by key or an episodic entry by ID. |
| `observe_pattern` | Both | `pattern_key`, `observation` | Record an observation about behavior or preferences for implicit learning. |

### Internal automatic extraction tools

The background memory extractor uses these four additional tools. They are not
offered as ordinary agent or builder tools by `list_tools`.

| Tool | Parameters | Purpose |
| --- | --- | --- |
| `store_semantic_memory` | `key`, `content`, `importance?` | Store or update a fact; importance defaults to 0.7. |
| `store_episodic_memory` | `content`, `importance?` | Store an event; importance defaults to 0.6. |
| `record_observation` | `pattern_key`, `observation` | Record an implicit-memory observation. |
| `no_memory_changes` | `reason` | Explicitly report that no new durable information was found; offered in strict extraction mode. |

## Application tools and generated actions

The catalogue also grows with your application:

- `@app.tool()` exposes typed Python functions as tools. Their schemas come from
  type annotations and their descriptions from docstrings or decorator options.
  They are available to the agent; the builder has a restricted tool set.
- Activated skill actions appear under their returned `action_*` callable names,
  with the input schema defined by their version. Executions pin action versions;
  explicitly activating a version in that execution updates its pin.

These names are application-specific and are not part of the 37 built-ins above.
See [the framework guide](FRAMEWORK.md) for decorators and custom HTTP endpoints.

The registration sources are [the engine registry](../fluxyr/tools/registry.py),
[web tools](../fluxyr/tools/web.py), [memory access](../fluxyr/core/memory_tools.py),
[memory write schemas](../fluxyr/core/brain_tool_executor.py) and
[automatic extraction](../fluxyr/core/memory/manager.py).

## Reactive inbox and normalizers

See [Reactive routines](REACTIVE_ROUTINES.md) for collection, payload formats,
session correlation, test/activation and replay semantics. These native tools
are available to the agent; technical normalizers themselves are never model tools.

| Tool | Inputs | Purpose |
| --- | --- | --- |
| `list_routine_receipts` | `routine_id`, `before?`, `limit?` | Paginate IDs/status without loading payloads. |
| `inspect_routine_receipt` | `routine_id`, `receipt_id`, `offset?` | Read a bounded JSON-text slice of payload, attempts and linked jobs. |
| `create_routine_normalizer` | `routine_id`, exactly one of `source`/`source_path`, `dependencies?` | Save a technical Python version; paths are within data_dir. |
| `inspect_routine_normalizer` | `routine_id`, `version_id`, `offset?` | Read saved source and metadata in bounded JSON-text slices. |
| `test_routine_normalizer` | `routine_id`, `receipt_id`, `version_id` | Dry-run against a collected sample, without dispatch. |
| `configure_reactive_routine` | `routine_id`, `mode?`, `normalizer_id?` | Configure collection/activation/disable; active requires tested version. |
| `replay_routine_receipt` | `routine_id`, `receipt_id` | Explicit replay using active version; can cause real agent actions. |

## Optional headless browser

Enabled with `FLUXYR_BROWSER_ENABLED=true`; see [installation and private input](BROWSER.md).

| Tool | Context | Parameters | Purpose |
| --- | --- | --- | --- |
| `browser` | Both | `tool`, `arguments_json?` | Call public-browser locally; `help` lists supported operations and `help` with `{"tool":"navigate"}` returns that operation's schema. `capture_image` returns a file for `read` or `render_preview`; `close` releases Chrome. Use `type` only for public text. |
| `browser_fill_private` | Both | `origin`, `ref` or `selector`, `vault_item_id`, `field?`, `submit?` | Resolve a Vault value and fill the exact field through private pipes. TOTP items generate a fresh code just before filling. Returns outcome only. |
| `browser_request_input` | Both | `origin`, `ref` or `selector`, `title?`, `vault_item_id?`, `field?`, `submit?` | Pause for private human input, or explicit consent to use a specified Vault item. The card is also surfaced in the caller's conversation when a builder requests it. |
| `browser_register_passkey` | Both | `origin`, `ref` or `selector`, `suggested_name?`, `vault_item_id?` | Ask human confirmation, then register through the site's button and save the key directly in Vault. For recovery after a failed save, provide the pending Vault ID and omit the button. |
| `browser_use_passkey` | Both | `origin`, `vault_item_id`, `ref` or `selector` | Restore a Vault passkey privately and click the passkey-login button. Returns assertion/storage outcome, not a guarantee of server acceptance. |

## Continuous worker routines

See [Worker routines](WORKER_ROUTINES.md) for the Python context contract, lifecycle, durable delivery and examples. Use `create_routine(trigger="worker", ...)` first.

| Tool | Inputs | Behavior |
| --- | --- | --- |
| `create_routine_worker` | `routine_id`, exactly one of `source` / `source_path`, `dependencies?`, `secrets?` | Save immutable listener code defining `run(ctx)`. Source paths are relative to Files/data. Secrets are exact Vault item names. |
| `inspect_routine_worker` | `routine_id`, `version_id?`, `offset?` | Read configuration and bounded logs, or one code version. JSON-text slices expose `next_offset`. |
| `test_routine_worker` | `routine_id`, `version_id`, `seconds?` | Test 1–30 seconds (default 5) after environment setup. Real network/Vault, samples only, no agent jobs or live checkpoint update. Stop any running listener first. |
| `configure_routine_worker` | `routine_id`, `version_id?`, `mode?`, `restart?` | Select code, collect, activate tested code, stop, or reconnect/reset failures. |

Existing receipt inspection, replay, normalizers and session APIs also work with worker routines. Human-in-the-loop requests are handled by downstream executions, not inside the continuous listener.
