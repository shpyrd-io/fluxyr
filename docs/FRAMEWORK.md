# Fluxyr as a framework

A consumer application uses the same engine as the bundled workbench. Install a
built wheel in the application's own virtualenv; no Node installation is required
when consuming the package. The optional [headless browser](BROWSER.md) requires
Node and Chrome/Chromium.

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
| `DATABASE_URL` | SQLite at `<root>/state/fluxyr.sqlite3`; optional SQLite or PostgreSQL SQLAlchemy URL |
| `FLUXYR_AGENT_NAME` | `Default Agent`; instance name displayed in the sidebar |
| `FLUXYR_APPROVALS_API_KEY` | Empty disables approval authentication. When set, requires Bearer authorization for approval listing, attached previews, decisions and requested credential saves; other APIs remain open. See [approval API](APPROVALS_API.md). |
| `FLUXYR_PROVIDER` | `openrouter`; also `openai`, `anthropic`, `custom` |
| `FLUXYR_MODEL` | Required when starting the agent worker; no implicit model |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` | Key for the selected provider required when starting workers |
| `FLUXYR_ROOT` | `.fluxyr` relative to the launch directory; one root for data, state, cache, runtime and workspace (e.g. `/var/lib/fluxyr` on a volume) |
| `FLUXYR_SKILLS_DIR` | Empty disables file skills; otherwise path relative to the launch/project directory, or absolute; independent of `FLUXYR_ROOT` |
| `FLUXYR_BROWSER_ENABLED` | `false`; enable optional [browser tools and private Vault/OTP input](BROWSER.md) |
| `FLUXYR_HOST` | `127.0.0.1` |
| `PORT` | `5050`; an explicit `app.run(port=...)` wins |
| `FLUXYR_WORKERS`, `FLUXYR_TOOL_WORKERS` | `4`, `6`; each 1–32. `WORKERS` bounds execution workers globally; a routine's `max_concurrency` adds a per-routine limit. Human waits release execution slots. `TOOL_WORKERS` controls tool calls within a model batch. |
| `FLUXYR_HTTP_THREADS` | `24` |
| `FLUXYR_MAX_CONTENT_LENGTH` | `4194304` bytes |
| `FLUXYR_LEASE_SECONDS` | `90`; minimum 30, above the supervisor heartbeat interval |
| `FLUXYR_BASH_MAX_OUTPUT_BYTES` | Maximum captured output per Bash call; `33554432` (32 MiB) |
| `FLUXYR_TOOL_TIMEOUT` | `300` seconds, Python actions and file/shell tools |
| `FLUXYR_EXECUTION_MODE` | `local` (default) or `landlock`; Linux write confinement, see [execution protection](EXECUTION_PROTECTION.md) |
| `FLUXYR_WORKSPACE_RETENTION_DAYS` | `30`; startup and daily cleanup of old temporary workdirs, `0` disables |
| `FLUXYR_MAX_ITERATIONS` | `40` |
| `FLUXYR_PROVIDER_ENDPOINT` | Empty uses the built-in endpoint; required for `custom` |
| `FLUXYR_PROVIDER_FORMAT` | Built-in provider format; required for `custom`: `openai` or `anthropic` |
| `CUSTOM_API_KEY` | Required for `custom`; use a placeholder for a local endpoint that ignores authentication |
| `FLUXYR_MAX_TOKENS` | `16000` |
| `FLUXYR_REASONING_EFFORT` | Empty uses provider default |
| `FLUXYR_THINKING_MODE`, `FLUXYR_THINKING_BUDGET` | `none`, `0`; Anthropic thinking or OpenRouter reasoning |
| `VAULT_ENCRYPTION_KEY` | Optional; otherwise generated/persisted at `<root>/state/vault.key` |
| `FLUXYR_LOG_LEVEL` | `INFO` (CLI) |
| `FLUXYR_APP` | Optional CLI import target; `--app` wins |

`--init` initializes the database and directories without starting workers or
requiring a provider key. The UI never returns credential values or the database URL.
For an existing installation, move model options/provider credentials previously
saved through Settings into its environment before restarting.


### Instance storage and volumes

By default all generated instance storage is under `.fluxyr/` in the directory
where the process starts. Set `FLUXYR_ROOT` to another relative or absolute path
to relocate the whole instance. It does not relocate application code, `.env` or
relative `FLUXYR_SKILLS_DIR` paths.

```text
.fluxyr/
  data/                 Files, uploads and previews
    tmp/browser/        Browser captures/downloads, retained for 30 days
  state/                Default fluxyr.sqlite3, vault.key and layout marker
  cache/                Rebuildable Python environments and browser controller
  runtime/              Browser profiles, certificate scratch files, maintenance marker
  workspace/            Temporary execution directories, with job-aware retention
```

`state` and `data` are persistent. Keep the existing `VAULT_ENCRYPTION_KEY` when
provided externally; otherwise back up `state/vault.key` with the database.
Never apply age-based deletion to the whole root. `workspace` stays outside Files
and uses job status as well as age to decide what can be deleted. Logs go to stdout
unless your process manager redirects them.

The Dockerfile sets `FLUXYR_ROOT=/var/lib/fluxyr`. Mount one volume there:

```sh
docker run --rm --env-file .env -p 5050:5050 \
  -v fluxyr-data:/var/lib/fluxyr fluxyr
```

An explicit `DATABASE_URL` is still authoritative. PostgreSQL remains external;
an explicitly configured SQLite file outside this root needs its own persistence.

#### Migrating an existing installation

The default directory and the internal storage layout have changed. Startup
detects a legacy `.runtime` directory and refuses to silently create a fresh
database/key. Stop **all** old server, HTTP-only and worker processes first.
Use the old instance root as `--from`, not its `data` directory:

```sh
fluxyr migrate-storage --from . --to .fluxyr          # preview only
fluxyr migrate-storage --from . --to .fluxyr --apply  # copy and verify
```

For an existing Docker volume, migrate inside the mounted root instead:

```sh
fluxyr migrate-storage --from /var/lib/fluxyr --to /var/lib/fluxyr --apply
```

The command preserves originals, verifies copied files with SHA-256 and uses
SQLite's backup API (including committed WAL data) plus an integrity check.
It checks the old SQLite worker lock and, when `DATABASE_URL` is PostgreSQL, the
database worker lock. It refuses destination merges and symlink traversal.
Stop HTTP-only processes too: they do not hold the worker lock.

Then set `FLUXYR_ROOT` to the destination (or remove an old `FLUXYR_ROOT=.` to use
the new default). An explicit `DATABASE_URL` is not rewritten: if it points at the
old default SQLite file, remove it to use `state/fluxyr.sqlite3`, or update it
explicitly. Keep external PostgreSQL configuration and external encryption keys.
Relative skill folders now resolve from the launch directory; use an absolute
`FLUXYR_SKILLS_DIR` if yours previously lived under a custom storage root.

Python virtual environments are rebuilt on demand because they contain absolute
paths. Run `python -m fluxyr.browser install` again for a user-installed browser
controller; bundled development/Docker controllers remain usable. Disposable old
profiles and caches are retained at the source but are not copied. Verify the new
instance before archiving old storage, and never run both copies as separate
instances against the same external database.

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
`data/`. Dependency environments are cached separately under `cache/envs/`.

At worker startup, after acquiring the database ownership lock and before
dispatching jobs, Fluxyr removes workspace entries older than
`FLUXYR_WORKSPACE_RETENTION_DAYS` (30 by default; 0 disables). A tree is retained
if any contained file/directory was modified recently, its job finished recently,
or its job is nonterminal (including paused/waiting/building). Legacy
`workspace/tests/` invocations are pruned individually, only when no job is pending.
Symbolic links are never followed; a symlinked workspace root is not cleaned.
Mounted subdirectories and entries that cannot be inspected are retained. Cleanup
also runs daily on the maintenance thread. It does not run on package import,
HTTP-only initialization or each request. Workspace retention leaves database history, `data/` and dependency caches untouched.

Separately, the embedded worker cleans `data/tmp` every 24 hours, deleting files
with a modification time older than 30 days and old empty directories. New browser
captures/downloads live in `data/tmp/browser`. This maintenance runs on its own
sleeping thread, persists its last run under `runtime`, and never follows
symlinks or scans the operating system's `/tmp`. Other `data/` paths are persistent;
move files out of `data/tmp` when they must be kept longer.

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

See [Custom application tools](APPLICATION_TOOLS.md) for complete examples,
parameter schemas, database access, errors, concurrency and human-in-the-loop
workflows with `@app.tool()`.

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
The agent waits for the answer and makes a subsequent tool call; a native
function's Python stack is not suspended. See the
[custom-tool interaction example](APPLICATION_TOOLS.md#human-in-the-loop-with-custom-tools)
for instructions and the distinction between conversational input and enforced
approval.
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

### Worker recovery and health probes

A monitor independent of the dispatch thread supervises the embedded worker.
After failure it stops dispatch and waits for the previous generation's jobs,
Python subprocess owners, continuous routine listeners, reactive processor, memory worker and detached model
requests to finish. Only then does it reacquire the database/file fence and start
a generation with a new ownership token. Recovery never automatically replays
interrupted actions: external effects may already have occurred.

Recovery attempts use delays of 1, 5 and 15 seconds. A completed fenced heartbeat
and dispatch cycle resets the failure count; creating a thread does not. Three
unsuccessful recovery generations mark the process unhealthy. If old work cannot
drain within thirty seconds, the monitor also marks it unhealthy rather than
starting an overlapping worker. Applications with native tools should make long
operations interruptible where possible.

A database connection outage leaves the HTTP interface available while reconnecting.
Failed reconnections wait thirty seconds and do not consume the worker-defect
retry budget. Restarting containers cannot repair an unavailable database.

- `GET /api/health/live`: HTTP 200 while recovery remains viable; HTTP 503 after
  recovery exhaustion or unsafe/stuck drainage. Use this for **liveness**.
- `GET /api/health/ready`: HTTP 200 only when the worker is running; HTTP 503 while
  stopped, starting, recovering or failed. Use this for **readiness** when the
  deployment should route traffic only to a functioning agent.
- `GET /api/health`: compatibility endpoint with version and session metadata,
  `worker`, `worker_state`, `recovery_failures` and `live`. Its HTTP status follows
  liveness; `status` is `degraded` when the worker is unavailable.

The deployment must configure its probes to use these endpoints; installing the
package does not change Kubernetes or hosting-provider probe configuration.

Continuous connections can run as [Worker routines](WORKER_ROUTINES.md), outside the agent execution pool. They emit durable events into the existing per-session queue.
