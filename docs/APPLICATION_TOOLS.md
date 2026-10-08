# Custom application tools

Use `@app.tool()` to make a Python function available to the agent. Fluxyr builds
its input schema from type hints and uses its docstring to explain when and how
to call it. Your application owns the implementation and dependencies.

## Register a tool

```python
from typing import Annotated, Literal

from pydantic import Field
from fluxyr import Fluxyr

app = Fluxyr(__name__)


@app.tool(parallel_safe=True, side_effecting=False)
def convert_temperature(
    value: Annotated[float, Field(description="Temperature to convert")],
    source: Literal["celsius", "fahrenheit"] = "celsius",
) -> dict:
    """Convert a temperature between Celsius and Fahrenheit."""
    if source == "celsius":
        return {"value": value * 9 / 5 + 32, "unit": "fahrenheit"}
    return {"value": (value - 32) * 5 / 9, "unit": "celsius"}


if __name__ == "__main__":
    app.run()
```

Save this as `app.py`, configure the model and provider key as described in the
[first application guide](../README.md#your-first-application), and run `python app.py`. The agent can now
call `convert_temperature`. The decorator also works without parentheses:
`@app.tool`.

Register all tools before `initialize()`, `start()`, `run()` or the first HTTP
request. Accessing `app.engine` or `app.db` also initializes the application.

| Decorator option | Default | Meaning |
| --- | --- | --- |
| `name` | Function name | Name exposed to the model. |
| `description` | Function docstring | Explain the purpose, prerequisites and result. One of these is required. |
| `parallel_safe` | `False` | Allow concurrent execution with other eligible tool calls. |
| `side_effecting` | `True` | Track the invocation through the effect ledger. Set to `False` for read-only or pure operations. |

Names must be ASCII Python identifiers of at most 64 characters. The `action_`
prefix and existing engine/memory tool names are reserved. Duplicate names fail
initialization or registration. Application tools are available to ordinary agent
executions; the isolated skill builder has its own restricted tool set.

## Parameters and results

Every parameter needs a type annotation. Parameters without defaults are required;
a default makes a parameter optional. `str | None` allows null, but still requires
the argument unless you also give it a default, such as `= None`.

Supported annotations include scalars, lists, `Literal`, `Annotated` constraints
and nested Pydantic models. For example, add this before `app.run()`:

```python
from pydantic import BaseModel, ConfigDict


class ReportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=100)]
    sections: list[str]


@app.tool(parallel_safe=True, side_effecting=False)
def summarize_report(options: ReportOptions, note: str | None = None) -> dict:
    """Summarize report settings without creating or sending a report."""
    return {"options": options.model_dump(mode="json"), "note": note}
```

The agent sends JSON; Fluxyr validates it before calling Python and supplies an
actual `ReportOptions` instance. Unknown top-level arguments are rejected.
Validation is strict: a string such as `"42"` is not accepted for an integer.
Positional-only parameters, `*args` and `**kwargs` are not supported. There are no
automatically injected job, session or request parameters.

Return JSON-compatible values: dictionaries, lists, strings, numbers, booleans or
`None`. Convert models with `model_dump(mode="json")` and dates to ISO strings.
NaN and infinity are not valid results. A return annotation documents intent;
Fluxyr does not validate the result against it.

Return the domain result itself; Fluxyr adds its execution envelope. To report an
operation failure, raise an exception with a useful, non-sensitive message:

```python
raise ValueError("Unknown report ID; list available reports before retrying")
```

The engine records a failed tool result with the exception type and message.
Returning `{"success": False}` instead is ordinary output inside a successful
native invocation; it does not mark the tool as failed. Return values and errors
can reach the model and execution history, so keep credential values out of both.

## Database access, async and side effects

Tools execute in a Flask **application context**, with access to `current_app`,
application services and `app.db.transaction()`. They do not have an HTTP request
context: use explicit arguments instead of reading `flask.request`.

```python
from sqlalchemy import text


@app.tool(parallel_safe=True, side_effecting=False)
def database_time() -> dict:
    """Read the current timestamp from the application database."""
    with app.db.transaction() as session:
        timestamp = session.scalar(text("SELECT CURRENT_TIMESTAMP"))
    return {"timestamp": str(timestamp)}
```

Create a separate transaction per invocation; do not share a SQLAlchemy session
between concurrent calls. Enable `parallel_safe=True` only if your function and
its dependencies tolerate concurrency. It permits parallel execution; it does
not force the agent to issue calls together.

`async def` functions are supported. Fluxyr awaits the result using an event loop
owned by that invocation, so create and close loop-bound clients inside the call
rather than sharing them across invocations.

Application tools run inside the server process, using its installed dependencies,
environment and permissions. They do not use the generated-action subprocess or
its isolation. Set explicit timeouts on network/database operations. An arbitrary
native Python function cannot be forcibly stopped safely by the engine.

For writes or external operations, keep `side_effecting=True`. The effect ledger
tracks outcomes, but does not make an external API transaction atomic or replace
its idempotency keys. Neither decorator flag grants or enforces user approval.
Drain or cancel pending native operations before deploying incompatible code.

## Human in the loop with custom tools

Custom tools can participate in an interaction through the built-in `ask_human`
tool. The agent calls your function to gather information, asks the human, and
then calls your next function with the answer. Fluxyr persists the pending
question and resumes the conversation after the user responds.

For example, expose a report formatter that expects the user's preferred style:

```python
@app.tool(parallel_safe=True, side_effecting=False)
def format_report(
    title: Annotated[str, Field(min_length=1, max_length=100)],
    style: Literal["brief", "detailed"],
) -> dict:
    """Format a report using the user's chosen style.

    If the user has not chosen a style, first call ask_human with the choices
    Brief and Detailed. Wait for the answer, map it to brief or detailed, then
    call this tool. Do not guess the preference.
    """
    return {"title": title, "style": style}
```

For a longer workflow, place the instructions in `skills/reports.md` and set
`FLUXYR_SKILLS_DIR=./skills`:

```markdown
---
name: reports
description: Prepare reports using the user's preferred style.
---
When preparing a report:
1. Obtain the report title from the user; ask_human can collect missing text.
2. If the user has not chosen a style, call ask_human with question
   "Which report style would you like?" and choices ["Brief", "Detailed"].
3. Wait for the answer. If the user declines or cancels, do not proceed.
4. Map Brief to "brief" and Detailed to "detailed", then call format_report
   with the title and style. Report its actual result.
```

The model's tool call in step 2 has these arguments:

```json
{"question": "Which report style would you like?", "choices": ["Brief", "Detailed"]}
```

`ask_human` accepts a question of 1–500 characters and 2–6 optional plain string
choices, each 1–100 characters. Omit `choices` for free-text input. Put long
explanations in the preceding message instead of option labels. Use
`manage_vault_credential` for private credential entry, rather than asking for
secrets through `ask_human`.

This is **conversation-level coordination**: your Python method completes before
the question, and a subsequent model turn chooses the next tool call. Its stack
and local variables are not suspended. Carry necessary data in explicit arguments
or persist it in your application's database. Calling `input()` inside a tool
would block a worker; it does not open a human-input card.

The docstring/skill instructions guide the agent; they are not an authorization
gate. For a sensitive operation, enforce permission in your application against a
trusted, persisted decision bound to that operation. An `approved=True` argument
supplied by the model is not evidence of human approval. The decorator currently
has no `requires_approval` option or native pause/resume API.

### When the action itself must pause

Generated, versioned Python actions have a different execution contract. Inside
those scripts, the runner provides `request_input`, `request_choice` and
`request_confirmation` helpers. They persist the interaction and re-run the same
pinned action version with its original arguments and saved replies, without a
model turn between those internal steps. Scripts restart from the top; they do
not resume a Python stack. Keep effects after the final interaction or protect
them with persistent checkpoints.

Those imports belong to the runner-provided `fluxyr` SDK; they are **not available
inside an `@app.tool()` function** in the installed framework. Returning a custom
dictionary from a native function does not turn it into a waiting interaction.
Use the [action contract](ACTIONS.md#complete-local-action) for deterministic
in-action interactions and preflight `requires_approval`, or keep conversational
questions between custom tool calls as above. See also the
[human interaction contract](HUMAN_INTERACTION.md) and
[approval HTTP API](APPROVALS_API.md) for responding from another application.

## Calling and testing your function directly

The decorator returns the original function, so ordinary Python calls and Flask
routes can reuse it. A direct call bypasses tool schema validation, tracing and
the effect ledger. Supply an application context if it uses Flask services.

Unit-test the function's business logic directly, including failures and
idempotency. For engine-level behavior, the repository's
[framework tests](../tests/test_framework.py) demonstrate validated calls,
parallel execution, application context and persisted tool results with a
scripted provider, without paid model requests.
