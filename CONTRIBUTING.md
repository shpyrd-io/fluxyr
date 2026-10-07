# Developing Fluxyr Agent

Fluxyr Agent is MPL-2.0 licensed; see LICENSE and retained third-party notices in
NOTICE. Contribute framework changes in this repository, or extend an application
without modifying the package using its public decorators.

## Editable installation (engine and application development)

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Set DATABASE_URL, FLUXYR_PROVIDER, FLUXYR_MODEL and the provider API key.
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
fluxyr --reload
```

The editable install resolves `fluxyr` to this checkout. To develop your own
application against the checkout, activate that application's virtualenv, run
`pip install -e /path/to/fluxyr`, then run
`fluxyr --app app:app --reload` from the consumer project.

Python and Markdown changes restart the process; `.env` changes are read by the
new process. Registering/importing the app in the reloader parent does not start
another engine. Reload drains/stops the previous worker before the new process
acquires its database worker lock. In-flight generated actions may be interrupted;
use disposable development data instead of production schedules.

For frontend hot reload, run `pnpm --dir frontend dev` in a second terminal.
Vite proxies API/preview requests to `http://127.0.0.1:5050`. Set
`FLUXYR_DEV_BACKEND=http://127.0.0.1:5059` if developing against another port.
Production wheels contain a built UI and do not need Node/pnpm at runtime.

## Validation

The default suite is fast and uses SQLite. It checks API behavior, configuration,
versioning, persistence, action contracts and human interaction without external
services or model calls:

```sh
pytest -q --durations=10
pnpm --dir frontend test
pnpm --dir frontend build
```

Real TLS, reload, process isolation, parallel Python and end-to-end HTTP workflows
are marked `integration`. Run everything locally with:

```sh
pytest -q -m '' --durations=15
# Also verify PostgreSQL using a disposable database:
TEST_DATABASE_URL=postgresql+psycopg2://user:password@localhost/test_db pytest -q -m ''
```

`pytest -m integration` runs only the integration group. Linux runs Landlock
checks; unsupported platforms skip those. PostgreSQL tests create/remove isolated
schemas. `TEST_INSTALL_DEPENDENCIES=1` opts into network dependency installation;
`scripts/validate_live_*.py --run` explicitly makes paid model requests.

Pushes and pull requests run the fast SQLite suite, UI tests/build and package
consumer checks on Python 3.11/3.13. The **Full integration tests** workflow runs
on demand and is also required before every release. It exercises the complete
suite with PostgreSQL on Linux; it is not repeated for every ordinary edit.
Stdlib-only actions create isolated environments without bootstrapping pip;
actions with declared dependencies still install and cache their environment.

## Build a distribution

```sh
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
python -m build
```

This produces a wheel and source distribution under `dist/`. The wheel build
fails if the UI has not been built. Editable backend development is exempt.
The source distribution includes the Python package, built UI, frontend source,
lockfile, docs, examples and licenses. It excludes `.env`, data, credentials,
virtualenvs, execution workspaces and test evidence.

Test the wheel in a new virtualenv outside the repository:

```sh
python3 -m venv /tmp/fluxyr-consumer-venv
/tmp/fluxyr-consumer-venv/bin/pip install dist/fluxyr-0.1.0-py3-none-any.whl
# In a separate directory, copy examples/minimal/app.py, skills/ and .env.example.
# Configure .env, then:
/tmp/fluxyr-consumer-venv/bin/fluxyr --app app:app
```

Installing a local wheel is the package-consumer workflow before a release exists
on an index. Publishing to PyPI is a separate release action; build/install does
not publish anything. See docs/FRAMEWORK.md for configuration and lifecycle details.

See [the release guide](docs/RELEASING.md) for version bumps and publication.
