# Changelog

## Unreleased

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
