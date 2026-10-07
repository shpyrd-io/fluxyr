# Fluxyr Agent extraction

Source: fluxyr-work2, commit 019eceb0b82ccc955280671a6813ba602cc70c0a.

## Accepted scope

One installation, one agent, one permanent workbench. Separate execution sessions
share skills, encrypted credentials and local data. No user/account identity,
authentication, billing, gateway API, remote executor, S3, Redis or Pipedream.
Python tools may install packages. Full vault types, native OAuth and local
previews are included. Human actions park durably and release worker capacity.

## Architecture

Flask serves a React application and a JSON/SSE API. SQLAlchemy sessions are
explicit and never reassigned globally. PostgreSQL stores conversations, queued
jobs, versioned skills/tools, schedules, human decisions, effects and events.
A supervisor thread dispatches independent sessions into a bounded pool; one
active owner per session. Tool batches preserve upstream parallel-safe dispatch.
Python scripts run in subprocesses with isolated dependency environments and
access to data/. This is trusted local execution, not an OS security sandbox.

## Implementation phases and acceptance

1. Extract brain, adapters, parallel dispatch, pending-action gate, web reader and
   TechDoc. Their imports must contain no Fluxyr account/billing dependencies.
2. Add PostgreSQL state, durable event replay, session queues, leases, heartbeat,
   effect claims and human continuation. Duplicate decisions cannot repeat a run.
3. Add local Python execution, package installation, encrypted vault, OAuth,
   certificate validation, file operations and local preview responses.
4. Add workbench skill creation/build/rebuild, candidate validation and immutable
   version selection for active jobs; manual/cron runs with output validation.
5. Add web chat, execution details, human cards, skills, vault, files and settings.
6. Verify with real PostgreSQL and deterministic test adapters: concurrent sessions,
   ordered same-session turns, restart while waiting, duplicate approvals, effects,
   cancellation, local scripts, validation failure, scheduling and SSE replay.

No existing Fluxyr customer data or credentials are migrated. No commits or pushes
are made automatically. Model-backed smoke tests require operator-supplied keys.

## Workbench parity audit (2026-10-07)

The initial extraction preserved the brain but replaced the workbench/builder
prompts with a short custom prompt. It was not a faithful port of the complete
skill-building workflow. Deterministic tests did not establish model reliability.

The local prompts now adapt the source `backend/prompts/workbench.md`,
`backend/prompts/skill_build.md`, `backend/docs/tools/tools-guide.md`, and the
contracts in `build_tool_provider.py` and `skill_build_helpers.py`: research,
inspect existing skills, explain the action plan, write complete code one action
at a time, use the runtime helper correctly, inspect actual test output, and use
returned identifiers rather than inventing names. Local filesystem paths, package
installation and Vault references are described explicitly.

Remaining differences must not be described as upstream parity:

- The workbench now persists instruction/spec and delegates build/rebuild to a
  separate builder session with its own context and tools. The parent suspends
  durably and releases its worker, then tests and inspects the returned versions.
  This is separation of responsibilities, not independent model verification.
- Candidate/test/activate is a local version-publication lifecycle introduced in
  this extraction, not an upstream account/permission requirement. The original
  builder made created actions callable immediately.
- Python human interaction currently supports one response per invocation, not
  the original helper's multiple distinct rounds/context protocol.
- The source helper's credential write-back effects are not ported. Native OAuth
  refresh works in the backend; arbitrary action credential write-back does not.

The UI persists and replays model text and provider-supplied reasoning as distinct
blocks, interleaved with tools. It renders Markdown and follows the bottom only
while the reader remains there. Historical reasoning discarded by the old stream
callback cannot be recovered from text-only responses.

## Controls, memory and error contract

- Pause/cancel interrupts waiting for provider I/O and discards late responses before
  any tool dispatch. Provider transport is closed best-effort; remote billing or
  processing may not stop immediately. The number of in-flight requests is bounded.
- A running local Python process group can be suspended/resumed with POSIX signals.
  Cancellation kills the process group. Dependency setup is cancellable. Package
  setup finishes before a pending pause reaches the action boundary. Process pause
  is not a durable OS checkpoint; after process loss, recorded effects need review.
- Skill enable/disable/delete is available through workbench tools and the UI.
  Delete is a tombstone that retains action versions and execution audit records.
- Clear context archives session messages/jobs/events and clears all brain layers;
  it rejects unfinished jobs and preserves skills, routines, files and vault items.
- Semantic, episodic and implicit memory is automatically injected every model step,
  as in the original brain. Explicit read/delete tools and a read-only panel supplement
  this mechanism; no read tool is required to recall facts already in the prompt.
- Explicit memory writes commit before returning their tool result. Automatic
  extraction is queued transactionally with conversation completion in PostgreSQL,
  then processed on a dedicated thread outside the foreground worker pool. Builder
  sessions do not enqueue extraction. Saved memory is still injected into the next
  model step; this changes extraction timing, not recall into the conversation.
- Extraction uses the configured provider/model with a separate 2,048 output-token
  ceiling. Its input preserves the original user request and final answer, limits
  conversation content to 16,000 characters and existing memory content to 12,000,
  and omits structured source code and reasoning. These limits apply only to the
  extraction prompt; stored facts and the normal chat context are not truncated by
  this mechanism. The extractor must explicitly save or declare no new memories.
- Inferred results apply when the session is idle, in per-session queue order.
  A revision fence discards older extraction results after any explicit memory
  mutation or context clear, conservatively prioritizing the user's latest edits.
  Ready results survive restart; interrupted calls return to the queue. Extraction
  failures retry up to three attempts with backoff and remain inspectable afterward.
  Memory updates and extraction usage/decisions are recorded as separate events.
- Python handler results distinguish completion from execution success. Exceptions,
  nonzero exit, malformed/missing output, dependency errors and timeouts return
  success=false with the error. A result emitted before a later exception is not
  successful. Routine narratives cannot override these recorded action results.
- The invented routine expectation/schema/check evaluator has been removed from
  the active flow. Old columns remain for data preservation, but are not injected
  or used to judge new runs. Action requirements belong in the builder specification.
