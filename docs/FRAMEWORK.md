# Fluxyr as a framework

A consumer application uses the same engine as the bundled workbench. Install a
built wheel in the application's own virtualenv; no Node installation is required
when consuming the package.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install fluxyr
python app.py
# Equivalent, with Flask-style import targets and factories:
fluxyr --app app:app
fluxyr --app 'app:create_app()'
```

```python
from fluxyr import Fluxyr
from sqlalchemy import text

app = Fluxyr(__name__)

@app.tool(parallel_safe=True, side_effecting=False)
def server_time() -> dict:
    """Read the database server's timestamp."""
    with app.db.transaction() as db:
        value = db.scalar(text("SELECT CURRENT_TIMESTAMP"))
    return {"timestamp": str(value)}

@app.get("/api/example/time")
def time_endpoint():
    return server_time()

if __name__ == "__main__":
    app.run()
```

Copy [the minimal example](../examples/minimal/app.py) and its `.env.example`.
Only `app.py` is necessary when the deployment already sets its environment.
SQLite is included and used by default; PostgreSQL is optional. The workbench,
built-in tools, memory, vault, scheduler and Python builder are bundled.

## Configuration

The framework loads `.env` from the current working directory (where the process
was started). Existing process environment wins. Configuration is frozen for the
application instance; restart to apply edits. `Settings` in Python remains
available for test fixtures/embedding, not as an alternate persisted UI setting.
The Settings page is read-only. Old `configurations.model` rows and provider keys
in the vault no longer override the environment. The vault remains available for
credentials used by actions.

| Variable | Default / requirement |
| --- | --- |
| `DATABASE_URL` | SQLite at `<root>/.runtime/fluxyr.sqlite3`; optional SQLite or PostgreSQL SQLAlchemy URL |
| `FLUXYR_AGENT_NAME` | `Default Agent`; instance name displayed in the sidebar |
| `FLUXYR_PROVIDER` | `openrouter`; also `openai`, `anthropic`, `custom` |
| `FLUXYR_MODEL` | Required when starting the agent worker; no implicit model |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | Key for the selected provider required when starting workers |
| `FLUXYR_ROOT` | Current working directory. Optional override for `data`, `workspace`, `.runtime` (e.g. a Docker volume) |
| `FLUXYR_SKILLS_DIR` | Empty disables file skills; otherwise path relative to `FLUXYR_ROOT`, or absolute |
| `FLUXYR_HOST` | `127.0.0.1` |
| `PORT` | `5050`; an explicit `app.run(port=...)` wins |
| `FLUXYR_WORKERS`, `FLUXYR_TOOL_WORKERS` | `4`, `6`; each 1–32 |
| `FLUXYR_HTTP_THREADS` | `24` |
| `FLUXYR_MAX_CONTENT_LENGTH` | `4194304` bytes |
| `FLUXYR_LEASE_SECONDS` | `90`; minimum 30, above the supervisor heartbeat interval |
| `FLUXYR_BASH_MAX_OUTPUT_BYTES` | Maximum captured output per Bash call; `33554432` (32 MiB) |
| `FLUXYR_TOOL_TIMEOUT` | `300` seconds, Python actions and file/shell tools |
| `FLUXYR_EXECUTION_MODE` | `local` (default) or `landlock`; Linux write confinement, see [execution protection](EXECUTION_PROTECTION.md) |
| `FLUXYR_WORKSPACE_RETENTION_DAYS` | `30`; startup cleanup of old temporary workdirs, `0` disables |
| `FLUXYR_MAX_ITERATIONS` | `40` |
| `FLUXYR_PROVIDER_ENDPOINT` | Empty uses the built-in endpoint; required for `custom` |
| `FLUXYR_PROVIDER_FORMAT` | Built-in provider format; required for `custom`: `openai` or `anthropic` |
| `CUSTOM_API_KEY` | Required for `custom`; use a placeholder for a local endpoint that ignores authentication |
| `FLUXYR_MAX_TOKENS` | `16000` |
| `FLUXYR_REASONING_EFFORT` | Empty uses provider default |
| `FLUXYR_THINKING_MODE`, `FLUXYR_THINKING_BUDGET` | `none`, `0`; Anthropic thinking or OpenRouter reasoning |
| `VAULT_ENCRYPTION_KEY` | Optional; otherwise generated/persisted under `.runtime` |
| `FLUXYR_LOG_LEVEL` | `INFO` (CLI) |
| `FLUXYR_APP` | Optional CLI import target; `--app` wins |

`--init` initializes the database and directories without starting workers or
requiring a provider key. The UI never returns credential values or the database URL.
For an existing installation, move model options/provider credentials previously
saved through Settings into its environment before restarting.


### Provider endpoints and custom servers

Built-in endpoints live in the package: Anthropic `https://api.anthropic.com`,
OpenAI `https://api.openai.com/v1`, OpenRouter `https://openrouter.ai/api/v1`.
`FLUXYR_PROVIDER_ENDPOINT` overrides the API base URL, without appending the
operation path (`/messages` or `/chat/completions`). It replaces the previous
`FLUXYR_BASE_URL` variable; rename that entry when upgrading.

A custom server uses an explicit protocol, model and key, independently of the
three built-in providers:

```dotenv
FLUXYR_AGENT_NAME=My Custom Agent
FLUXYR_PROVIDER=custom
FLUXYR_PROVIDER_FORMAT=openai
FLUXYR_PROVIDER_ENDPOINT=http://127.0.0.1:8000/v1
FLUXYR_MODEL=my-local-model
CUSTOM_API_KEY=local
```

For an Anthropic-compatible server, choose `FLUXYR_PROVIDER_FORMAT=anthropic`
and its API base URL instead. The adapter appends `/v1/messages`. Custom keys
never fall back to a built-in provider's credentials. Compatible endpoints must
support the selected protocol's streaming and tool calling.

### Workspace retention

`workspace/` is disposable execution storage: copies of action source and runtime
helpers, plus scratch files. Action versions, results, job state and human replies
are persisted in the database. Artifacts that must survive executions belong in
`data/`. Dependency environments are cached separately under `.runtime/envs/`.

At worker startup, after acquiring the database ownership lock and before
dispatching jobs, Fluxyr removes workspace entries older than
`FLUXYR_WORKSPACE_RETENTION_DAYS` (30 by default; 0 disables). A tree is retained
if any contained file/directory was modified recently, its job finished recently,
or its job is nonterminal (including paused/waiting/building). Legacy
`workspace/tests/` invocations are pruned individually, only when no job is pending.
Symbolic links are never followed; a symlinked workspace root is not cleaned.
Mounted subdirectories and entries that cannot be inspected are retained. Cleanup
does not run on package import, HTTP-only initialization or each request. It leaves
Database history, `data/` and dependency caches untouched.

### Thinking and reasoning

With Anthropic format, `FLUXYR_THINKING_MODE=adaptive` sends
`thinking.type=adaptive`; `enabled` sends a `budget_tokens` value taken from
`FLUXYR_THINKING_BUDGET`. With OpenRouter, `adaptive` sends
`reasoning.enabled=true`, and `enabled` sends `reasoning.max_tokens`.
`FLUXYR_REASONING_EFFORT`, if set for OpenRouter, takes precedence over these two
options. With OpenAI format, effort is sent as `reasoning_effort` (custom servers
must support it). `none` means omit Fluxyr's thinking override, not force the
provider to disable reasoning. All options are subject to the selected model's
support; no effort level is inferred from the model name.

Omitting the thinking/effort variables leaves provider defaults untouched, including
for MiniMax M3: Fluxyr sends no reasoning/thinking override. To explicitly enable
reasoning for MiniMax M3 through OpenRouter:

```dotenv
FLUXYR_PROVIDER=openrouter
FLUXYR_MODEL=minimax/minimax-m3
FLUXYR_THINKING_MODE=adaptive
FLUXYR_REASONING_EFFORT=
```

MiniMax's [Chat Completions documentation](https://platform.minimax.io/docs/api-reference/text-openai-api)
documents adaptive thinking for M3, but states that tunable reasoning effort is
effective only for M3.1-Flash-Preview. Do not interpret OpenRouter accepting an
effort label as evidence of distinct M3 thinking depths. OpenRouter's
[reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
describes `enabled=true` as enabling reasoning with model defaults.

## Markdown skills

Every `*.md` beneath the configured directory is loaded at initialization, in
sorted order, including nested folders. UTF-8 text is the skill instruction.
Optional YAML front matter supplies `name` and `description`:

```markdown
---
name: server-clock
description: Read time from the database.
---
For database time, call server_time and report the returned value exactly.
```

Without front matter, the name is the relative file path without `.md`. File
skills are listed by `list_skills`, readable by `get_skill`, visible in the UI,
and included in the job's skill snapshot/context. IDs are `file:relative/path.md`.
They are not copied into database skills. Duplicate file names or collisions with
existing database skill names fail initialization explicitly. File skills cannot
be edited, disabled, deleted or built by the agent; change/remove the file and
restart. Database skills retain the existing build/test/activate workflow.
Removing a file does not remove any historical job snapshot or generated action.
Markdown supplies instructions; Python functions/actions supply executable code.

## Application tools

`@app.tool()` returns the original function and registers its callable name,
docstring and typed parameters with the harness. Optional decorator arguments:
`name`, `description`, `parallel_safe=False`, `side_effecting=True`.

Type hints are required for every parameter; defaults become optional schema
fields. `Annotated`, `Literal`, lists, optional values and nested Pydantic models
are supported. Input is validated before calling Python; no implicit string to
number coercion. Positional-only parameters and `*args/**kwargs` are rejected.
Return JSON-compatible data. Async functions are supported through an event loop
owned by the tool invocation. Docstrings/explicit descriptions explain usage to
the model. Engine/memory tool names and the `action_` prefix are reserved.

Calls go through the normal tool lifecycle, trace, error handling and effect
ledger. Tools execute in the application's Flask context, so `current_app`,
`app.db.transaction()` and application services work in worker threads. Use a
separate database transaction per invocation, not a shared SQLAlchemy Session.
Declare `side_effecting=False` for read-only functions and opt into
`parallel_safe=True` only when concurrent calls do not race on shared state.

Application tools run in-process, with application dependencies and permissions;
they are not versioned/generated subprocess actions. Their code changes with the
application deployment. Use timeouts in network calls; an arbitrary native Python
function cannot be forcibly paused or killed safely. The generated action SDK's
pause/resume helpers belong to subprocess actions. Native tools do not use that
SDK automatically; use the existing `ask_human` tool for conversational questions.
Do not deploy code changes over pending native side effects; drain/cancel jobs first.

## HTTP and lifecycle

`Fluxyr` subclasses Flask: `route`, `get`, `post`, blueprints, `request`,
`jsonify`, `app_context` and SQLAlchemy transactions work as usual. Engine HTTP
endpoints live in the `fluxyr` blueprint. Register custom tools/routes/blueprints
before initializing. Route/tool collisions are rejected; extension endpoints
should use their own prefix such as `/api/example/`.

Construction and decorators do not connect to the database, create runtime folders
or start threads. `initialize()` connects, validates and registers engine routes.
`start()` additionally starts the supervisor/worker pool, and is idempotent.
`close()` stops workers and disposes connections, and is idempotent; create a new
application after closing it. `run()` starts both engine and Waitress, handles
shutdown, and uses environment host/port/thread settings.

The application is WSGI-callable. A raw WSGI request lazily initializes HTTP only;
it does not start workers. For deployment, prefer `app.run()` / `fluxyr` in
one process. For an external WSGI host, call `app.start()` after process creation
in exactly one process and `app.close()` at shutdown. Never start workers before
forking. PostgreSQL uses an advisory lock per schema; SQLite uses an OS file lock
beside its database to reject a second supervisor. SQLite uses WAL and serialized
transactions, including atomic queue claims. Independent model/tool execution
still runs concurrently; database writes are short and serialized. Use a local
filesystem for SQLite, and PostgreSQL for larger workloads. Multiple installations
require separate schemas/databases and runtime roots.

## Validation

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the fast SQLite suite and complete
PostgreSQL/Linux integration suite. Releases check both databases, supported
Python versions and an installed wheel outside the source checkout.
