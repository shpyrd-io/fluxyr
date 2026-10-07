# Local Python action development guide

## Structured tool payloads

For open-ended JSON payloads, prefer the explicitly JSON-encoded fields: create_action.parameters_json, test_action.params_json, and finish_execution.output_json. Send a string containing valid JSON and omit the corresponding object field. This is supported by this engine and decoded before validation; it avoids ambiguity in provider encoding of nested values. For example, params_json should contain `{"count":4,"sides":6,"drop_lowest":1,"enabled":false}`, not quoted numeric/boolean values. The original object fields remain supported. Do not infer that a provider used XML or that this engine stringifies values from a type error; report only the received type and actual validation result.

The final report's output_json is the actual task result. The report is narrative evidence only; it never supplies a business-success verdict. Tests that fail before dispatch do not exercise the code. Never claim unexecuted scenarios were tested "behind the scenes".

All file tools and Python data_dir share the SAME persistent data root. Tool paths are already relative to it: read_file(path="sales.csv") and data_dir / "sales.csv" address the same file. Do not prefix paths with data/ or ./data/. Use list_files(path=".") for the root. A script's fresh working directory is separate from data_dir; data_dir does not change between tests and routine executions. Inspect existing input files before testing; do not replace them with invented example data unless the user requests that.

An action is a Python script, not a function definition returned to the caller. Top-level `return` is invalid. Use a main function or raise SystemExit to stop early. Write complete code, with no placeholders or fabricated API responses.

```python
from fluxyr import params, data_dir, output, log

# params is a dictionary, data_dir is a pathlib.Path.
value = params["value"]
log("Processing input")
output({"value": value})
```

Call output with one JSON-serializable result, exactly once. It prints the result but does not terminate the script. Use log for progress and raise a useful exception on failure. Do not silently return empty success data when an API, parse or file operation failed. Validate required values and API responses; set network timeouts and handle HTTP failures.

Declare parameters as an object JSON Schema with properties, required fields and useful descriptions. The runner validates it before execution. Match code lookups to these names. Declare third-party packages as pip requirements in dependencies; the runner installs them in a virtualenv for that version. Standard library modules need no dependency entry. Do not run pip from action source. Code runs locally in a fresh working directory per invocation; use data_dir for persistent files. Resolve caller-supplied paths under data_dir and reject paths that escape it. Preserve existing data unless replacement is part of the requested operation.

For example, this is a valid parameters object (enum and required are arrays; boolean defaults and input values are JSON booleans, never strings):

```json
{"type":"object","properties":{"category":{"type":"string","enum":["all","ai"],"default":"all"},"avoid_recent":{"type":"boolean","default":true}},"required":[],"additionalProperties":false}
```

Call that action with `{"category":"ai","avoid_recent":false}`, not `{"avoid_recent":"false"}`. Always import params explicitly before using it; it is not a predeclared global.

For credentials, declare the Vault item names in the action's secrets array, then use `from fluxyr import secret` and `secret("item_name")`. This returns the item's content dictionary, not a JSON string. A text credential uses its value field; an access token uses access_token. OAuth refresh is handled by the backend. Do not implement refresh inside an action or write credentials back through ordinary file tools. Never output or log secret values. The old remote helpers `helper`, `workspace_helper`, `approval_helper`, and schema directives x-vault/x-encrypted are not this runtime's API.

For human interaction, use ask_human in the workbench. Inside an action use `from fluxyr import request_human` and `answer = request_human("Question")`. The first call suspends the script; after the response the script restarts from the beginning and the helper returns that response. Put effects after the question or implement persistent checkpoints/idempotency. This runtime currently supplies one response per action invocation: do not write a sequence of different questions in one script, because they would receive the same response. Split that workflow into separate actions and workbench questions. Set requires_approval when execution needs an explicit preflight decision, especially for destructive or externally visible changes, and honor rejection.

Python virtualenvs isolate packages, not the operating system. Treat generated code as trusted installation code. You have no remote executor, account context, integration marketplace, S3, or remote workspace. HTML and other previews must be written under data_dir and exposed with render_preview.

## Execution result and failures
Call output(value) with the specified result shape. Implement validation and error conditions from the action spec in Python. Raise a meaningful exception when the operation fails; never swallow exceptions and print a success-looking result. The local execution handler automatically captures uncaught exceptions, stderr and non-zero exit codes as success=false with the error and logs. Normal completion with a valid output returns success=true. done records completion separately. Do not emit or ask a model to supply a success flag, and do not defer error detection to a routine expectation.
