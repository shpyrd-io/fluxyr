# Local Python action development guide

## Structured tool payloads

For open-ended JSON payloads, prefer the explicitly JSON-encoded fields: create_action.parameters_json, test_action.params_json, and finish_execution.output_json. Send a string containing valid JSON and omit the corresponding object field. This is supported by this engine and decoded before validation; it avoids ambiguity in provider encoding of nested values. For example, params_json should contain `{"count":4,"sides":6,"drop_lowest":1,"enabled":false}`, not quoted numeric/boolean values. The original object fields remain supported. Do not infer that a provider used XML or that this engine stringifies values from a type error; report only the received type and actual validation result.

The final report's output_json is the actual task result. The report is narrative evidence only; it never supplies a business-success verdict. Tests that fail before dispatch do not exercise the code. Never claim unexecuted scenarios were tested "behind the scenes".

All file tools and Python data_dir share the SAME persistent data root. Relative paths use that root as cwd: read(path="sales.csv") and data_dir / "sales.csv" address the same file. Do not prefix paths with data/ or ./data/. Use bash(command="ls -la") for the root. A script's fresh working directory is separate from data_dir; data_dir does not change between tests and routine executions. Inspect existing input files before testing; do not replace them with invented example data unless the user requests that.

An action is a Python script, not a function definition returned to the caller. Top-level `return` is invalid. Use a main function or raise SystemExit to stop early. Write complete code, with no placeholders or fabricated API responses.

```python
from fluxyr import params, data_dir, output, log

# params is a dictionary, data_dir is a pathlib.Path.
value = params["value"]
log("Processing input")
output({"value": value})
```

Call output with one JSON-serializable result, exactly once. It prints the result but does not terminate the script. Use log for progress and raise a useful exception on failure. Do not silently return empty success data when an API, parse or file operation failed. Validate required values and API responses; set network timeouts and handle HTTP failures.

`log(value)` and `output(value)` return None. Format text before passing it, for example `log(f"Attempt {attempt}")`; never call `log("...").format(...)`. `secret(name)` returns a dictionary or raises for an undeclared name; it does not return None. A top-level `success: false` in output is treated as execution failure even when Python exits zero; prefer raising the underlying failure and returning only the domain result.

Declare parameters as an object JSON Schema with properties, required fields and useful descriptions. The runner validates it before execution. Match code lookups to these names. Declare third-party packages as pip requirements in dependencies; the runner installs them in a virtualenv for that version. Standard library modules need no dependency entry. Do not run pip from action source. Code runs locally in a fresh working directory per invocation; use data_dir for persistent files. Resolve caller-supplied paths under data_dir and reject paths that escape it. Preserve existing data unless replacement is part of the requested operation.

For example, this is a valid parameters object (enum and required are arrays; boolean defaults and input values are JSON booleans, never strings):

```json
{"type":"object","properties":{"category":{"type":"string","enum":["all","ai"],"default":"all"},"avoid_recent":{"type":"boolean","default":true}},"required":[],"additionalProperties":false}
```

Call that action with `{"category":"ai","avoid_recent":false}`, not `{"avoid_recent":"false"}`. Always import params explicitly before using it; it is not a predeclared global.

For credentials, declare the Vault item names in the action's secrets array, then use `from fluxyr import secret` and `secret("item_name")`. This returns the item's content dictionary, not a JSON string. A text credential uses its value field; an access token uses access_token. OAuth refresh is handled by the backend. Do not implement refresh inside an action or write credentials back through ordinary file tools. Never output or log secret values. The old remote helpers `helper`, `workspace_helper`, `approval_helper`, and schema directives x-vault/x-encrypted are not this runtime's API.


## Human interaction inside an action

Human input is normal workflow, not just a permission gate. Keep one cohesive workflow in ONE action: prepare → pause → receive the human response → continue → output. Do not split it into prepare/reveal actions or ask the workbench to carry state just to cross a pause. The runtime resumes the same pinned action version, original parameters and tool-call identity, without a model step between its interaction rounds.

Use these local helpers from `fluxyr`, each with a distinct, stable `key`:
- `request_input(title, key="topic", placeholder=None, context=None)` → `{decision: "complete", user_input: str, context: dict}`.
- `request_choice(title, candidates, key="style", message=None, context=None)` → `{decision: "complete", index: int, label: str, value/content/path if supplied, context: dict}`. Supply 2–6 label strings or objects with label and optional value, description, preview_type, content/path. This is a single-choice UI, NOT a text field. Never use topic text as a style or vice versa.
- `request_confirmation(title, key="confirm", message=None, context=None)` → `{decision: "approve", context: dict}`.
All three can return `{decision: "rejected", reason: str, context: dict}`; handle it explicitly, output a declined result and stop before effects.

On first encounter a helper pauses the process. After the user replies the script RESTARTS FROM THE TOP. Earlier helper calls return their saved responses; a new key can pause again, up to five rounds per invocation. Never generate random keys. Local Python variables do not survive; put small prepared values in context and use the returned context after resume. Choices return the ORIGINAL saved option, even if preparation regenerates different candidates. Put large artifacts under data_dir with unique immutable names and carry relative paths. Do not perform non-idempotent effects before or between pauses: earlier code is replayed. The runtime prevents repeated completed tool calls, not arbitrary statements inside the script.

Example of one action asking two different things:
```python
from fluxyr import request_input, request_choice, output

def main():
    topic = request_input("Qual é o tema?", key="topic")
    if topic["decision"] == "rejected":
        output({"declined": True}); return
    style = request_choice("Escolha o estilo", ["Aventura", "Mistério"], key="style")
    if style["decision"] == "rejected":
        output({"declined": True}); return
    # All required responses now exist; perform the final work here.
    output({"topic": topic["user_input"], "style": style["label"]})
main()
```

A choice may include a preview: `{label: "Layout A", preview_type: "html", path: "drafts/layout-a.html"}`. Supported preview types: html, image, text, file. Paths are relative to data_dir. Text uses content; HTML can use content for small snippets (stored locally by the runtime) or a path. Images/files require a local path. The UI displays these inside the choice card; render_preview alone only displays a file and does NOT pause or capture a selection. Never use remote/S3 URLs, inline credentials, or external resources in a preview.

`request_human("Question")` remains a compatibility free-text shorthand returning `{answer: str}`; prefer the explicit helpers for new actions. Do not create approval-response parameters in the action schema: continuation is supplied privately by the runtime. `ask_human` is for workbench/build-time clarification, not for replacing a skill's internal interaction. `requires_approval` is separate preflight approval; approving it does not answer an in-action question. Use it when the action requires authorization before starting, and honor rejection.

Python virtualenvs isolate packages. Deployments can additionally use Linux Landlock to restrict writes to data_dir and the current invocation directory. Treat generated code as trusted installation code, inherit deployment environment variables, declare dependencies in the action specification, and keep all writes within those directories. You have no remote executor, account context, integration marketplace, S3, or remote workspace. HTML and other previews must be written under data_dir and exposed with render_preview.

## Execution result and failures
Call output(value) with the specified result shape. Implement validation and error conditions from the action spec in Python. Raise a meaningful exception when the operation fails; never swallow exceptions and print a success-looking result. The local execution handler automatically captures uncaught exceptions, stderr and non-zero exit codes as success=false with the error and logs. Normal completion with a valid output returns success=true. done records completion separately. Do not emit or ask a model to supply a success flag, and do not defer error detection to a routine expectation.

File and shell operations: use read(path, offset?, limit?) for paginated text or images; write(path, content) creates or overwrites files and creates parent directories; edit(path, edits=[{oldText, newText}]) makes unique, non-overlapping replacements against one original file and returns a diff. Read before editing. Use bash(command, timeout?) for listing, finding, searching, moving, deleting, and local commands. All four share the data root as cwd and accept absolute paths; ~ means the server user home. Each bash call starts a fresh shell; cd does not persist. Output is bounded: read supplies a next offset; truncated bash output supplies a full log path that read can open. Do not loop on a truncated first oversized line; use bash to extract a bounded slice. Respect deployment filesystem write protection. General files do not replace versioned actions: build_skill delegates implementation, and the builder must register/test actions through create_action/test_action.
