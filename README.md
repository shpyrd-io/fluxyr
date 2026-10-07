# Fluxyr

A Python framework for building agents with tools, skills, memory and a web workbench.
Create an application with a Flask-style API; Fluxyr handles conversations,
streaming, background executions and scheduled routines.

## Install

```sh
pip install fluxyr
```

Requires Python 3.11+. The package includes the web UI; Node is only needed when
contributing to the frontend.

## Your first application

Create a `.env` file:

```dotenv
FLUXYR_MODEL=minimax/minimax-m3
OPENROUTER_API_KEY=your-key
```

Create `app.py`:

```python
from fluxyr import Fluxyr

app = Fluxyr(__name__)

@app.tool()
def greet(name: str) -> dict:
    """Greet someone by name."""
    return {"message": f"Hello, {name}!"}

@app.get("/api/hello")
def hello():
    return greet("Fluxyr")

if __name__ == "__main__":
    app.run()
```

Run `python app.py` and open **http://localhost:5050**.
You can also start the bundled workbench directly with `fluxyr`.

## Configuration

Fluxyr reads `.env` from the directory where you start the application.
Environment variables take precedence. Only the model and its provider's API key
are required.

| Variable | Default |
| --- | --- |
| `FLUXYR_MODEL` | Required |
| `OPENROUTER_API_KEY` | Required for the default provider |
| `FLUXYR_PROVIDER` | `openrouter`; also supports `openai`, `anthropic` and `custom` |
| `PORT` | `5050` |
| `FLUXYR_AGENT_NAME` | `Default Agent` |
| `DATABASE_URL` | SQLite at `.runtime/fluxyr.sqlite3`; PostgreSQL is optional |
| `FLUXYR_ROOT` | Current working directory; optionally relocate runtime data |
| `FLUXYR_SKILLS_DIR` | Optional folder of Markdown skills |

With another provider, supply its key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or
`CUSTOM_API_KEY`. See the [configuration reference](docs/FRAMEWORK.md#configuration)
for provider endpoints and other options.

## Skills and integrations

Point `FLUXYR_SKILLS_DIR` at a folder to load its `.md` files as agent instructions.
Use `@app.tool()` to expose Python functions and standard Flask routes for your API.
The workbench can also build and test Python actions, manage credentials in Vault,
request human input and schedule routines.

- [Minimal application](examples/minimal)
- [Framework API and configuration](docs/FRAMEWORK.md)
- [Python actions and credentials](docs/ACTIONS.md)
- [Human interaction](docs/HUMAN_INTERACTION.md)
- [Docker and execution protection](docs/EXECUTION_PROTECTION.md)

## Development

```sh
git clone https://github.com/shpyrd-io/fluxyr.git
cd fluxyr
pip install -e '.[dev]'
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
fluxyr --reload
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for local development and test suites, and
[the release guide](docs/RELEASING.md) for versioning and publication.

Licensed under [MPL 2.0](LICENSE). Third-party notices are in [NOTICE](NOTICE).
