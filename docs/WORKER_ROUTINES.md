# Continuous worker routines

A `worker` trigger runs a long-lived Python listener: WebSocket clients, chat connectors, mail subscriptions or uptime monitors. It waits for external events and emits work into Fluxyr's durable routine inbox. Waiting does not call a model or occupy an agent execution slot.

Each routine has one listener subprocess. The existing routine `overlap` and `max_concurrency` settings control the **agent executions produced by events**, independently of that listener. Conversations with the same `session_key` retain context and execute in order; distinct identities can run concurrently. Human decisions happen in those downstream conversations.

## Set up

1. Create a routine with trigger **Worker · continuous** and the prompt for handling events.
2. Save a listener version: Python source, pip dependencies and exact Vault item names. No credential values belong in the source.
3. Smoke-test it, then inspect received samples. A test uses real network/Vault access but creates no agent jobs and never changes the live checkpoint. Stop an existing listener before testing.
4. Choose **Collect samples** to listen without dispatch, or **Activate tested version** to process new events. Activation requires a successful smoke test. Old collected samples require explicit replay.
5. **Stop listener** disables intake. **Reconnect / reset failures** restarts the selected version. Status and bounded diagnostic history refresh on demand.

You can start collecting raw samples before creating a normalizer. The agent can inspect those receipts and add/test the normalizer later, exactly as with [reactive webhook routines](REACTIVE_ROUTINES.md).

## Python contract

Define `run(ctx)` or `async def run(ctx)`. Do not invoke it at module level. The host invokes it once per connection attempt. Return or an uncaught exception is a disconnection and triggers supervised retry.

| API | Behavior |
| --- | --- |
| `ctx.routine_id` | Stable routine UUID. |
| `ctx.test` | Whether this is a bounded smoke test. Do not fake successful connection or skip validation in tests. |
| `ctx.ready()` | Report a successfully established connection/subscription. Required for a successful smoke test. |
| `ctx.stopping` | Whether graceful shutdown was requested. |
| `ctx.wait(seconds)` | Interruptible wait; returns true on shutdown. Minimum 0.01 seconds. Use meaningful intervals, not busy polling. |
| `ctx.emit(session_key=..., event_id=..., payload=..., checkpoint=None)` | Persist a normalized event. Returns a receipt acknowledgement after commit, not after agent execution. |
| `ctx.receive(envelope, event_id=..., checkpoint=None)` | Persist raw input using the webhook envelope (`json`, `text`, `form`, `query`, `content_type`). Active routing requires a tested normalizer; collection does not. |
| `ctx.checkpoint` | Previously persisted JSON cursor, or `None`. |
| `ctx.save_checkpoint(value)` | Save a non-secret JSON cursor separately. Prefer `emit`/`receive` with a checkpoint for atomic event + cursor persistence. |
| `ctx.secret(name)` | Resolve a declared Vault item. Returns the same structured fields as an action's `secret(name)`, via a local socket pair, without model context or a credential file. |

Context methods are synchronous. In an async listener, use `await asyncio.to_thread(ctx.emit, ...)` (and similarly for other blocking context methods), preserving event order. Use finite connect/read timeouts and close sockets in `finally` or context managers. The supervisor requests graceful stop, then kills the process group after two seconds if necessary.

`session_key` and `event_id` must be non-empty strings up to 255 characters. Include the provider account in both when IDs are only unique within an account. Each listener is limited to 20 context requests per second using backpressure on its local channel. Payloads/envelopes are bounded to 256 KiB; checkpoints to 16 KiB. Do not put credentials in payloads, checkpoints or logs. Vault resolution supports OAuth refresh; resolve again when a long-lived connection needs a renewed token.

The inbox keeps up to 1,000 receipts per routine and retains finished samples for seven days. Pending receipts are never rotated to make room. Intake also rejects new events while the routine has 1,000 unfinished agent jobs. A full queue raises an error; wait and retry the **same event ID** without acknowledging it upstream or advancing the cursor. Transport errors can happen after commit: retry with the same ID.

External event IDs deduplicate downstream delivery for 30 days. Receipt acknowledgement guarantees local persistence only. Reconnect recovery depends on the provider's replay/cursor or a catch-up API; a live WebSocket alone does not guarantee delivery during downtime. Checkpoints survive restarts, but the listener must implement the provider-specific resume protocol. This is not exactly-once external execution.

## Uptime example

See [`examples/continuous_worker/uptime.py`](../examples/continuous_worker/uptime.py). It uses the standard library, waits 60 seconds between checks and emits only when availability changes. Save the file through the routine editor or `create_routine_worker` (the tool's `source_path` must be inside Files/data). Monitoring configuration is pinned with the immutable code version.

## AgentMail example

[`examples/continuous_worker/agentmail_listener.py`](../examples/continuous_worker/agentmail_listener.py) uses the [AgentMail WebSocket SDK](https://www.agentmail.to/docs/websockets). Declare `agentmail` as a dependency and `AgentMail API key` as a Vault item of type `text`, with its `value` holding the API key. Replace the inbox ID before saving. It waits for subscription confirmation, filters incoming mail, and correlates by inbox + thread while deduplicating by inbox + message.

The example requires your AgentMail account; it is not a live-provider integration test. It does not implement message-history catch-up. Its event includes bounded text and message IDs; downstream actions can fetch full message content when needed. The local integration tests exercise real socket delivery, receipt persistence, separate identities, replay and subprocess shutdown without an external service.

## Runtime and operations

Only the engine process holding the worker ownership fence starts listeners. HTTP-only processes do not start them. Per-routine locks also prevent a test and a listener from running simultaneously. Up to eight listeners/tests run per host manager; additional configured listeners wait for capacity. They have cached dependency environments and use the configured `local`/`landlock` execution protection. Scratch files live under `FLUXYR_ROOT/runtime/listeners` and are removed when the process exits.

The supervisor reads a small configuration projection every five seconds. On PostgreSQL, each live listener also checks its ownership connection every five seconds; a lost connection stops that subprocess. Idle listeners wait on IO; logs/status are persisted only when changed, at most once every five seconds. Each run retains at most 16 KiB of logs. History retains 20 finished runs and the API displays the latest 10; code versions are immutable and retained. These limits avoid replaying a continuous stream into the UI.

Short failures retry with increasing delays, then stop after five consecutive failures. A connection that stayed ready for at least 60 seconds resets the failure streak on its next failure. Listener failures are reported on the routine and do not poison the agent worker's health. Restart the listener explicitly after fixing its code or credentials. The engine drains listeners before releasing its ownership fence. Linux children are also killed if their parent process disappears.

A listener must report ready within 60 seconds of starting Python. Smoke tests last 1–30 seconds after dependency setup. They validate startup and the observed connection window, not long-term reliability or every event shape. They cannot undo side effects performed by custom Python. Keep listeners focused on intake, filtering and durable delivery; implement business operations as normal actions.

## HTTP and native tools

- `GET /api/routines/{id}/worker`: configuration, version metadata and latest runs.
- `PATCH /api/routines/{id}/worker`: `version_id`, `mode` (`collecting`, `active`, `disabled`), optional `restart: true`.
- `POST /api/routines/{id}/worker/versions`: `source`, optional `dependencies` and `secrets` arrays.
- `GET /api/routines/{id}/worker/versions/{version}`: one full code version.
- `POST /api/routines/{id}/worker/versions/{version}/test`: optional `seconds` (default 5).

The existing receipt, normalizer, replay and session endpoints also support worker routines. Worker routines do not expose a POST webhook token. See [native tools](TOOLS.md) for agent-facing equivalents.

This feature adds schema version 7. Back up the database before upgrading; older Fluxyr versions cannot open the upgraded schema.
