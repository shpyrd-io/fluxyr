# Changelog

## 0.9.6 — 2026-10-08

- Add continuous Worker routine triggers with supervised Python listeners, private Vault access, durable event/checkpoint acknowledgements, collection and smoke tests, bounded diagnostics, and reuse of reactive session routing and concurrency. Add UI/API/native tools and examples for uptime and AgentMail. Upgrade database schema to 7.

- Consolidate instance storage under `.fluxyr` by default (override with `FLUXYR_ROOT`), separating persistent SQLite/Vault state, Files, caches, runtime scratch and execution workspaces. Add an explicit verified migration command and refuse silent initialization over detected legacy storage.
- Resolve relative skill folders from the launch directory, independently of storage. Run job-aware workspace retention daily as well as at startup.

## 0.9.5 — 2026-10-08

- Add light, dark and system themes with a header selector, browser persistence and automatic operating-system appearance updates.

- Store new browser captures and downloads under `data/tmp/browser`, using full UUIDs encoded as 22-character base62 capture names. The embedded worker removes files older than 30 days from `data/tmp` once daily, outside the execution queue.

- Add bounded parallel routine execution with queued overflow, per-session ordering, human-wait slot release, and the existing global worker cap. Enforce queue mode when workers claim jobs.
- Add concurrency controls to routine forms and native tools. Migrate database schema to version 6 with a default per-routine concurrency of 1; older engines cannot open the upgraded database.

## 0.9.4 — 2026-10-08

- Register passkeys inside Fluxyr's browser with human confirmation and direct encrypted Vault storage; restore them for later WebAuthn sign-in without exposing private keys to the model.
- Persist signature counters and support retrying a failed Vault save while the browser retains the newly created credential.

## 0.9.3 — 2026-10-08

- Add encrypted TOTP Vault items, private browser field filling and session-routed human input without putting values in model tool arguments or results.
- Add an optional public-browser 3.0 headless Chrome controller using local pipes, explicit page captures, bounded session lifetimes and an optional Docker browser target.
- Render relative screenshot paths through the Files preview route, including WebP images in existing chat messages.

## 0.9.2 — 2026-10-08

- Recover failed workers in-process after draining the previous generation, reacquiring ownership, and preserving interrupted actions without replay. Add separate liveness/readiness probes and bounded recovery attempts.
- Coalesce browser metadata refreshes, suppress refreshes during historical replay, pause hidden-tab polling, and use compact job summaries plus paginated incremental usage responses.
- Reduce database polling, project only worker control fields, invalidate pause/cancel caches immediately, batch stream fragments, and index usage/event replay queries.

## 0.9.1 — 2026-10-07

- Preserve conversation context after worker interruption or execution exceptions while closing unresolved calls without retrying them.
- Include persisted tool failures and execution phases in diagnostics even when no script logs exist, and stop showing interrupted tool calls as running.

## 0.9.0 — 2026-10-07

- Add reactive routines with durable webhook collection, versioned Python normalizers, sample testing, session routing, deduplication and explicit replay.
- Bound webhook sample storage and display 20 summaries per inbox page, loading payloads only when opened.
- Expose `app.receive_event()` for authenticated custom receiver routes and native tools for building and inspecting reactive routines.
- Document custom `@app.tool()` functions, typed arguments, execution behavior and human-in-the-loop orchestration.
- Upgrade database schema to version 5 for reactive routines; existing cron routines migrate automatically. Older engine versions cannot open the upgraded database.
- Keep progress marks live-only, discard completed activity and update the activity panel independently of conversation history.
- Add a pending approval API and optional `FLUXYR_APPROVALS_API_KEY` Bearer authorization for listing/responding, with an in-app key prompt.
- Add `get_current_datetime` for current date/time and timezone-aware relative-date context in agents and builders.
- Show tool-owned token usage and cost in conversation and execution cards, including historical TechDoc calls, without repeating the initiating model request's usage.
- Render local PDFs inside Files and conversation previews without relying on the browser PDF plugin; open PDFs and images as previews instead of text.
- Guide credential setup with descriptive Vault names and explicit, consistent environment selection.
- Preserve acquired conversation context when cancelling a job, without resuming cancelled tools or forms.
- Prefill public OAuth settings in private credential forms for creation and editing.
- Expose human-question and choice-label limits in tool schemas and agent instructions.
- Keep embedded credential forms inside their interaction card and adapt columns to available width.

## 0.1.0 — 2026-10-07

First public framework release.

- Flask-compatible `Fluxyr` application with tool decorators, HTTP routes and database access.
- Markdown skills, isolated builders, immutable action versions, testing and activation.
- Persistent workbench, streaming execution sequence, parallel tools and scheduled routines.
- Vault with OAuth client credentials, mTLS certificates and private embedded credential forms.
- Resumable human interactions and execution pause/cancel controls.
- Local Python/file/Bash tools and optional Linux Landlock write protection.
- Packaged web UI, environment configuration, Docker and editable development examples.
- SQLite defaults, working-directory runtime storage, PORT and explicit model/key configuration.
- Fast CI suite and on-demand/release integration tests; faster stdlib-only action environments.
- One version source shared by the Python distribution, CLI and ENGINE VERSION display.
