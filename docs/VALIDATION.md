# Validation record

Implementation baseline: Fluxyr work2 commit `019eceb0b82ccc955280671a6813ba602cc70c0a`.
Validation date: 2026-10-07.

## Executed successfully

- **51 automated tests** in the complete regression run, plus a subsequent same-turn skill re-enable regression using a disposable PostgreSQL 18 cluster, isolated schemas, the project's own Python virtualenv, and `TEST_INSTALL_DEPENDENCIES=1`.
- End-to-end workbench flow: create a weather skill, generate a Python action, test against a local HTTP fixture, activate it, create the 08:00 UTC schedule, enqueue a manual run, fetch and log the forecast, validate its structured outcome, and publish the execution report to the main session.
- Per-session queue ownership with simultaneous claims; independent conversations remain claimable.
- Multiple human requests, duplicate decisions, approve/reject on different calls, durable waiting across engine reconstruction, and test-action human continuation.
- Pause after a completed file effect, resume without repeating it, cancellation of queued work without releasing another job's human wait, and expired-lease recovery without automatic re-execution.
- Effect result replay and refusal of uncertain previous dispatch.
- Local Python execution, version pinning, test-before-activation, actual pip dependency installation, and execution timeout even when a script never reads stdin.
- Vault encryption/key persistence, secret redaction, OAuth refresh serialization and rotating token preservation, PKCE state creation and single-use callback.
- Local file containment including escaping symlinks, conflicting edits, and sandboxed preview response headers.
- Python error-handler outcomes independent of model narrative; cron timezone behavior, duplicate-tick prevention and skip-overlap behavior.
- Concurrent read-only tool overlap with results retained in call order, explicit memory writes and pruning that preserves tool call/result pairs.
- Both real provider SDKs exercised through mock HTTP transports; OpenAI and OpenRouter request-option separation; TechDoc extraction and adapter routing without the Fluxyr gateway.
- Independent frontend dependency installation, TypeScript checking and production Vite build.
- Browser smoke test: chat send/response, skill creation, Python candidate editing, execution in an isolated session and activation after a passing test.
- Python wheel build and inspection: the distribution includes the built frontend and prompt resources.
- `pip check`, static undefined-name checks and `docker compose config --quiet`.

## Not exercised against external services

- Paid OpenRouter MiniMax M3 runs have now been performed; see the real-model results below. Live Anthropic and direct OpenAI requests have not been exercised.
- Third-party OAuth authorization/token servers and live mutual-TLS endpoints. OAuth transport was simulated; certificate parsing/conversion is implemented but a provider-specific mTLS handshake still needs its real credentials and endpoint.
- A real weather provider: the workflow test deliberately uses a controlled local API fixture.
- Container image build/start: Docker Desktop was manually paused on the host. Compose syntax was checked; the independently installed Python app and compiled web UI were run directly.

## Deliberate operating limits

- One application process, with an embedded supervisor and bounded thread pool. PostgreSQL advisory fencing prevents two supervisors for the same schema.
- No authentication or per-tool permission system. The process executes trusted installation code; Python virtualenvs do not provide OS sandboxing.
- Human input inside Python restarts the script with the response injected. Ask before performing effects, or implement action-level checkpoints/idempotency.
- Interrupted effects require operator review. External exactly-once semantics are not claimed.
- Missing schedule occurrences are coalesced. Jobs, events, working directories and versions have no automatic retention/deletion policy yet.
- HTML-reading browser tools are included; interactive Playwright browser automation and per-run VMs are not part of this initial engine.

## Additional checks on 2026-10-07

- Dedicated builder and rebuild scope, parent release/resume with one worker,
  migration from the original schema, and durable dependency resolution.
- Pause/cancel during a blocked provider call with no late tool dispatch; pause,
  resume and cancel of an actual local Python process without restarting it.
- Clearing/archiving context, restoring semantic memory into the model prompt,
  explicit reads that do not mutate memory, and disabled/deleted skill dispatch.
- Four frontend timeline tests, TypeScript/Vite production build, and browser
  verification of the tool catalogue, skill controls and context controls.

## Real-model results

These are distinct from tests using scripted adapters. No generated action source
was supplied to MiniMax. Inputs are compared against a separate Decimal-based
oracle, including inputs withheld until the model has finished coding.

- Earlier attempts exposed argument-type/schema mistakes and OpenRouter 402
  in-flight-budget failures; they were not successful acceptance runs.
- `d69ced0e92e6`: main workbench delegated to a separate builder, tested a candidate,
  received an actual Python traceback, revised the spec and rebuilt. It activated
  a version and ran a saved 08:00 UTC routine. The Python execution succeeded, but
  independent verification failed because city output preserved case instead of
  applying the requested casefold. The overall report correctly records failure.
- Evidence is retained under `.runtime/live-validation/<run id>/` (ignored), including
  tool traces and reports. Never interpret a model's final response as test evidence.

- Follow-up for `d69ced0e92e6`: the actual mismatch was returned to the main
  workbench as user feedback. The model updated the spec, delegated a rebuild,
  tested and activated it, and reran the existing routine. Both the original input
  and previously unseen Unicode/decimal/negative-value inputs matched the independent
  oracle. `followup-report.json` records success. This passed after feedback, not on
  the first attempt; model correctness is not inferred from runtime success.

- `bea1404ceac4`: real MiniMax saved the fictional balance `R$ 127,43` in semantic
  memory. The test then removed the conversation and episodic/implicit layers,
  reconstructed the engine from PostgreSQL, and asked for the balance without
  supplying its value. The answer was exactly `R$ 127,43`. See `memory-report.json`.

## Background memory extraction

- The complete PostgreSQL regression run passed **60 tests**, with dependency
  installation enabled. Static undefined-name checks also passed.
- Automated coverage checks foreground completion while extraction is blocked,
  builder exclusion, immediate explicit persistence, deletion/reset fencing,
  per-session ordering, ready-result recovery, bounded input and schema-4 migration.
  Additional checks cover retrying an empty decision, explicit no-change decisions,
  recovery of a running task and the three-attempt failure limit.
- The first real automatic-extraction fixtures (`436c4bee0ec6`, `858c22b800e4`)
  qualified the supplied city as fictional; neither saved the expected semantic
  fact. These reports record failures. The extraction contract now requires a
  structured save-or-no-change decision and records that decision for inspection.
- `14a3b618caae`: real OpenRouter MiniMax M3, one foreground worker, isolated
  PostgreSQL schema, no scripted adapter. A normal declarative fact about a synthetic
  persona was extracted automatically. Chat completion occurred 0.048 seconds after
  the last stream-close event, while extraction was still queued. Extraction used
  1,154 input and 33 output tokens and saved the city. After removing conversation,
  episodic and implicit context and reconstructing the engine, a real model call
  answered `Recife`. See `async-memory-report.json`; this single measurement is not
  a general latency guarantee. Run with `python scripts/validate_live_memory_async.py`.
