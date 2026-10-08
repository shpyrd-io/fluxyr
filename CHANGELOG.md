# Changelog

## Unreleased

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
