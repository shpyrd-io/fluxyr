# Fluxyr

A Flask-compatible Python framework and self-hosted agent workbench.
One Python application serves the web UI and runs an embedded worker. PostgreSQL is the only required service.

Build Python skills in a persistent chat, test and activate immutable action versions, run routines in separate conversations, and schedule them with cron. The installation has no accounts, authentication, billing, remote files or integration marketplace.

## Build your own application

```python
from fluxyr import Fluxyr

app = Fluxyr(__name__)

@app.tool(parallel_safe=True, side_effecting=False)
def greet(name: str) -> dict:
    """Greet someone by their name."""
    return {"message": f"Hello, {name}!"}

@app.get("/api/example/hello")
def hello():
    return greet("Fluxyr")

if __name__ == "__main__":
    app.run()
```

Install the [release wheel](https://github.com/shpyrd-io/fluxyr/releases) in your project's virtualenv, set `DATABASE_URL` and the
selected provider's API key, and run `python app.py`. Optional Markdown skills
load through `FLUXYR_SKILLS_DIR`. See [the framework guide](docs/FRAMEWORK.md),
[the minimal application](examples/minimal/app.py) and [development/package
instructions](CONTRIBUTING.md). This repository can also be installed with
`pip install -e '.[dev]'` for local contributions. See [releases and PyPI setup](docs/RELEASING.md) for versioning, publication and `requirements.txt` examples.

## Start with Docker

```sh
cp .env.example .env
docker compose up --build
```

Set the provider, model and provider API key in `.env` before starting, then open http://localhost:5050. Supported providers are Anthropic, OpenAI and OpenRouter. Settings displays the effective environment configuration read-only. PostgreSQL stores agent state; deployment settings come from the environment.

The Docker configuration keeps PostgreSQL internal and binds the web UI to loopback. Set `FLUXYR_BIND` to the desired interface when deploying on a private server. There is intentionally no login: anyone with network access to the web service can run trusted Python code on the installation. A reverse proxy/private network is an installation concern.

## Start from source

Requirements: Python 3.11+, Node 22+, pnpm 11, and a PostgreSQL database.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
cp .env.example .env
# Set DATABASE_URL to your PostgreSQL database in .env.
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
python -m fluxyr
```

The schema and local directories are created on startup. `python -m fluxyr --init` initializes them without serving HTTP. Run exactly one app process; the supervisor holds a PostgreSQL advisory lock to prevent another worker owning the same schema. Do not use a multiprocess Gunicorn deployment.

For frontend development, run `pnpm --dir frontend dev` alongside the Python app on port 5050.

## Workbench

A typical request:

> Create a weather skill for my provider's API, with a forecast action accepting a city. Ask me for the documentation URL and the credentials you need. Test it for New York. Then create a routine that runs every day at 08:00 UTC, retrieves the forecast, prints it in the execution log and returns the city and forecast as JSON.

The agent can research documentation with `web_browse`, `web_extract` and `tech_doc`; build skills and Python actions; read, write and edit local files and execute Bash; ask for human input; render local previews; and create, update or run routines. It can use every active action. There is no allowlist by account or external catalogue.

A skill contains usage instructions, a technical specification and one or more actions. The workbench saves them with `create_skill` and delegates to `build_skill`: a separate builder context researches, submits a plan and calls `create_action` to produce immutable candidates. The workbench resumes with the artifacts and inspects them. It does not write Python itself. `test_action` runs real Python with supplied inputs; a passing test is required before `activate_action`. The UI exposes the same lifecycle. Tests can have real effects. Updating an action creates a new version; executions already running retain their original versions. An execution that explicitly activates its own new action can use it immediately.

The workbench can disable/re-enable or delete skills while retaining execution history. **Tools** shows the actual available schemas. **Memory** shows saved memories, which the brain also injects automatically into each model step. **Clear context** archives the session history and clears its conversation and memory layers; skills, routines, files and vault items remain. Finish or cancel outstanding jobs first.

Explicit memory writes are committed before the agent confirms them. Automatic
memory extraction runs afterward on a separate, durable PostgreSQL queue, so it
does not hold the chat open. Automatically inferred facts become available after
that extraction finishes; explicit edits and context resets invalidate older
inferences. Builder sessions skip automatic extraction.

## Python actions

Source is a script with a JSON Schema for its parameters and a list of pip requirements:

```python
from fluxyr import params, data_dir, output, log, secret, request_human

log(f"Preparing forecast for {params['city']}")
credentials = secret('weather-api')  # declare this vault name in the action
# Call the documented API using the installed dependency or the standard library.
output({'city': params['city'], 'forecast': 'API result goes here'})
```

The example describes the contract; it does not invent a weather endpoint. See [the action contract](docs/ACTIONS.md) for a complete local action and OAuth/certificate content formats.

Each invocation has a separate working directory under `workspace/`; `data_dir` points to the shared `data/` directory. Dependencies install in managed virtualenvs. The child inherits the server's environment, including database and provider configuration; declared Vault secrets also arrive through the action payload. Home, cache and temporary paths point into the invocation directory. Logs and JSON output are bounded, and declared Vault secret values are redacted from returned text (arbitrary environment values are not automatically redacted).

Python is trusted installation code. `FLUXYR_EXECUTION_MODE=local` (default) uses the app's OS permissions. On Linux, `FLUXYR_EXECUTION_MODE=landlock` restricts action and file/shell tool writes to `data/` and its own invocation directory, including subprocesses. Unsupported kernels/container policies fail startup instead of falling back. This is protection against accidental filesystem changes, not a hostile-code sandbox or per-execution VM. See [execution protection](docs/EXECUTION_PROTECTION.md) for the policy, deployment and tests.

## Execution and human interaction

- One persistent workbench conversation; every routine run has an isolated conversation that can be inspected and continued.
- Serial execution within a conversation; up to `FLUXYR_WORKERS` independent conversations in parallel (default 4).
- Parallel eligible read-only tool batches, with configurable `FLUXYR_TOOL_WORKERS` (default 6). Mutating Python actions execute sequentially within a turn.
- Durable human requests and approvals, including multiple requests in the same batch. The worker is released while waiting. Decisions are idempotent and available through the API for future external approval channels.
- Pause/cancel interrupts a pending model wait before any late tool dispatch. A running local Python process group can be suspended and resumed without restarting; cancel terminates it. Remote provider processing may take time to settle after closing the connection. Browser/document requests retain their configured timeouts. Cancellation cannot undo external effects.
- Streaming and progress use an append-only PostgreSQL event log, replayed over SSE using `Last-Event-ID`. Execution reports appear in the main chat without writing to its brain concurrently. The agent can inspect complete stored execution details with `list_executions` and `inspect_execution`.
- A worker lease that expires marks the job **interrupted**, never silently re-executing it. Review recorded tool results before starting new work. Effect claims prevent replay of completed calls and refuse an uncertain previous dispatch. This is not an exactly-once guarantee for third-party APIs; use their idempotency keys for irreversible requests.

## Routines and success

A routine is a prompt using implemented actions, optionally scheduled. The Python execution handler returns `done` and `success` separately: an execution that ends with an exception, nonzero exit or invalid output returns `success: false` with the actual error and logs. The action's requirements and error conditions belong in its technical specification and generated Python. `finish_execution` may record narrative evidence but cannot override the handler's results. A routine with no executed action has no action-success verdict (`null`). There is no model-based expectation evaluator.

Cron expressions use five fields, with an IANA timezone (`UTC` by default). The scheduler persists the next occurrence and uses row locks and unique occurrence keys. Missed ticks after downtime are coalesced into one run. Overlap policy is `queue` or `skip`; queued runs get independent sessions and consume the bounded worker pool.

## Vault and local storage

The vault supports text, key/password pairs, OAuth2, access tokens, PEM certificates and PFX certificates. Contents use envelope encryption (AES-256-GCM plus a wrapped key). OAuth authorization uses state and PKCE; token refresh is serialized by a database row lock. Client credentials and refresh tokens are supported, with an optional client certificate for token requests.

Workbench and builders can call `manage_vault_credential` to open the shared Vault form inside the conversation. Saving encrypts the private fields and resumes the waiting job atomically; the model receives only the item name/ID. Builder requests are also visible in the parent workbench. Cancel resumes with rejection. Edit forms never load saved secrets; omitted fields keep their values.

The OAuth form selects **Authorization code** or **Client credentials**, token authentication (request body or HTTP Basic), and a PEM/PFX certificate from Vault. Client credentials requires client ID + secret and a token URL, without browser authorization. Certificate links use stable IDs. OAuth configuration changes invalidate cached tokens. mTLS is applied to token acquisition/renewal; actions calling resource APIs that also require mTLS must use the certificate there too. The shared [Vault guide](fluxyr/prompts/vault.md) documents all runtime field shapes and exact-name declarations.

Action creation rejects literal `secret(name)` references missing from its `secrets` array and declarations of unknown Vault names. Candidate tests use real configured credentials/network. Explicit `output({"success": false, ...})` is a failure even with exit code zero, and cannot pass a test or enable activation; new code should raise exceptions and output only domain results.

Without `VAULT_ENCRYPTION_KEY`, a persistent key is created at `.runtime/vault.key` with mode 0600. Back up that key **and** PostgreSQL. Losing the key loses access to the vault. Never change the configured key without re-encrypting existing items.

```text
data/               shared files and local preview artifacts
workspace/          per-invocation source and working files
.runtime/vault.key  installation encryption key
.runtime/envs/      managed Python virtualenvs
```

The agent uses Pi-style `read`, `write`, `edit` and `bash` tools, with `data/` as cwd and absolute paths supported. See [file tools](docs/FILE_TOOLS.md) for contracts, streaming, image input and process controls. The browser’s file API paths are restricted to `data/`, including symlink containment. Text edits support an `etag` to detect concurrent edits. Preview files are served directly; HTML runs in an iframe with a sandbox and restrictive CSP. No S3 grants, upload service, Redis or remote execution service is needed.

## Tests

```sh
pip install -e '.[test]'
TEST_DATABASE_URL=postgresql+psycopg2://user:password@localhost/test_db pytest -q
pnpm --dir frontend build
```

Use a disposable PostgreSQL database. Each test creates and removes its own schema. Without `TEST_DATABASE_URL`, SQLite is used for lightweight tests and PostgreSQL-only concurrency tests are skipped. Provider tests use scripted responses or mock transport; no paid provider request is made.

The updated extraction baseline and acceptance scope are recorded in [IMPLEMENTATION.md](docs/IMPLEMENTATION.md). [VALIDATION.md](docs/VALIDATION.md) distinguishes tested behavior from deployment and live-provider checks.

## License

Mozilla Public License 2.0 ([LICENSE](LICENSE)). Third-party components retain their notices ([NOTICE](NOTICE)).
