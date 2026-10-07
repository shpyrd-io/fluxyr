# Fluxyr

A Python framework for agents that expand their own capabilities and repair their
skills through a self-healing workflow. Describe what you need in the chat:
Fluxyr can research an integration, generate Python actions, test them and save them
as reusable skills.

Create an application with a Flask-style API; Fluxyr handles tools, memory,
conversations, streaming, background executions and scheduled routines.

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
The agent is ready for you to configure its skills,
credentials and routines and start a conversation.

## Build skills through conversation

Ask Fluxyr to create an integration in the chat, for example:

> Create a weather skill for my provider's API, with a forecast action accepting
> a city. Ask me for the documentation URL and help me configure any credentials
> in Vault. Test it for São Paulo, then create a routine that retrieves the
> forecast every day at 08:00 in America/Sao_Paulo.

Fluxyr turns the request into skill instructions and a technical specification.
A dedicated builder generates the Python actions and declares their dependencies
and Vault credentials. The agent inspects the generated code, runs tests,
checks the results and activates a tested version for future conversations and
routines. You can follow the build and execution progress in the UI.

The same loop supports **self-healing**: when an action fails, the agent can
inspect the execution, diagnose the problem, revise the specification and rebuild
the affected action. It tests the replacement before activation and can improve
the skill's instructions from what it learns. Each code change creates a new
version, preserving the previous versions and execution history. Missing
credentials or required user choices are handled through embedded dialogs.

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
The agent can also build and test Python actions, manage credentials in Vault,
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
