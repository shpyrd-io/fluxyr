# Fluxyr interface

The frontend vendors the actual Scificn Button, Badge, Panel, Card, Input, Textarea,
Label, Select, Checkbox, Switch, Separator, StatusGrid, Typography, Breadcrumb,
Dialog and ASCII Spinner components from https://www.scificn.dev/r. Registry
hashes and the source theme revision are in `frontend/src/ui/scificn-source.json`;
local adaptations are described in NOTICE.

## Component consistency review

The workflow uses Card/Header/Title/Content for each persisted node, preserving
fork/join connectors and navigation back to the conversation. Forms and filters
use Radix-backed Scificn Select, labels, checkboxes and switches; inputs and
textareas keep their original Scificn primitives. IBM Plex Mono is hosted locally
and used throughout the interface. Breadcrumb and the local worker/jobs StatusGrid
use the upstream components. Fluxyr's purple palette and supplied logo are retained.

All execution/tool failure badges use the shared Status component: `error` and
`failed` both display **Failed** in red; `succeeded` and `completed` display
**Completed**. Backend state values remain unchanged. Runtime stdout and dependency
installation events stay persisted but no longer become conversation entries.
They are available under **Diagnostics**, with execution and call IDs. Tool details
still expose structured results; execution/build links use the Fluxyr avatar.

Browser validation covered dropdown keyboard selection, weekly cron checkboxes,
enable-switch state, cancellation without saving, PEM upload/text fields, and
the existing failed event 17474. Its badge computed color is #ef909a; the timeline
has no horizontal overflow at the inspected desktop size. Diagnostics shows the
original runner logs while the conversation contains no inline progress logs.
19 frontend tests and the production TypeScript/Vite build passed. No new model
calls or changes to production resources were made for this UI validation.

Fluxyr overrides the library tokens in `frontend/src/style.css`, using the supplied
`assets/symbol.svg` and `assets/logo-text.svg`. The symbol is white on #756ce0
(rgb 117, 108, 224), with rounded corners. Fonts are bundled locally.

Tools are displayed as compact grouped rows with plain-language summaries,
search/category filtering and expandable parameter tables. The original tool
schema and full description remain available. Categories are navigation aids;
they do not change tool availability or permissions.

Streaming assistant text uses Scificn's ASCII spinner (120 ms frames) and removes
it when the stream closes. Reduced-motion preference disables frame cycling.

Validation: TypeScript and production Vite build; four existing timeline tests;
browser inspection of workbench, memory dialog, skill controls, Tools search and
parameter expansion, files, routines, vault and settings. Responsive layouts
checked at 1280x720 and 390x844. These UI checks did not execute or alter user
skills, secrets, routines or conversation data.

Skill builds now keep one timeline card keyed by the child job ID. Completion
reports update that card instead of appending another. The compact
`GET /api/jobs/<id>/build` projection reads the persisted plan, version IDs and
tool lifecycle events; it excludes Python source and tool output. The card polls
every 1.5 seconds until terminal, displays planning/research/generation/creation,
counts distinct actions rather than versions, and preserves pause, human-input,
failure and cancellation states. Historical builds recover their actual status
on reload without rewriting events. Created candidates do not imply passed tests
or activation.

Progress validation: eight build tests passed with PostgreSQL (including lifecycle
projection, retries, suspended/terminal states and the worker's completed build),
six timeline tests passed (including duplicate and reordered reports), and the
production TypeScript/Vite build passed. Browser verification used the existing
real `tech_topic_randomizer` build: one completed card with three created actions.
No new model execution was used for this UI correction.

## Resource forms and execution timeline (2026-10-07)

Skills, routines and Vault use searchable compact rows, filters, expandable
information and labelled create/edit/delete dialogs. Skill action/version editors
remain available inside each expanded skill. Routine deletion stops future
scheduling while preserving queued/running/completed jobs and their conversations.
Vault edits use a stable item ID and merge only entered fields; omitted encrypted
values are preserved and never returned to the browser. PEM certificates support
file upload or pasted PEM plus a private key and optional passphrase. PFX/P12
files are converted locally to the existing backend representation. File uploads
are limited to 2 MB each to fit the API request limit after base64 encoding.

Workbench and execution conversations share a 70/30 chat/timeline layout. On
narrow screens the timeline moves below the conversation. Timeline nodes link back
to their chat entries. The persisted `events` records now include
`tool_batch_start`, `tool_batch_end`, and batch ID / parallel flag / batch size on
tool lifecycle events. No database schema migration or separate graph database is
needed. Old events only produce forks when start/end overlap proves concurrency;
serial calls are never grouped merely because their timestamps are close. User
inputs, reasoning, agent answers, human questions/responses, tool names and skill
builds have distinct nodes. SSE replay is batched over 50 ms to avoid rerendering
both timelines for every individual historical token.

Validation: 66 PostgreSQL regression tests; 10 frontend projection/replay tests;
TypeScript and Vite production build. Browser checks created and edited a test
skill, created a disabled routine, uploaded a disposable password-protected PFX,
and renamed it without resubmitting its private contents. A disposable execution
ran actual `list_files` and `vault_list` tools through the concurrent dispatcher
with a scripted model adapter to verify fork/join rendering. This visual fixture
is not a real-model behavior test.

The ask_human regression reproduces the actual MiniMax payload containing
singleton empty-value option maps. These are normalized to their labels before
schema validation; other malformed structures remain rejected. Separately, a
real OpenRouter MiniMax M3 probe on isolated PostgreSQL produced the human question,
waited, accepted Celsius through the UI and resumed with the correct confirmation.
Evidence: `.runtime/live-validation/76f200427ba5/human-report.json`.

## Live activity across executions (2026-10-07)

The composer now shows actual model activity from its execution and descendants:
`.` for this execution's stream, `🛠️` for tool calls and green `•` for child/nested
streams. Each row
identifies the source execution, phase, tool, total fragments/characters and time
since its last event. Markers retain a bounded recent tail while counters retain
the total. Waiting for the provider before its first fragment is explicit.
Completed activity collapses into a history disclosure.

ActivityEmitter aggregates fragment counts every 250 ms and mirrors metadata into
ancestor sessions using persisted parent job/call IDs. It counts tool argument
streaming without persisting raw argument fragments. Child reasoning and Python
source remain in the child conversation. Techdoc model calls also emit nested
activity. Routine invocations publish an immediate execution card that later
completion reports update. These events replay from PostgreSQL without a schema
migration. Reasoning remains visible when supplied by the model.

Read-only inspection of the three latest production jobs found that the main
story_spinner build had no persisted reasoning and spent 54.5 seconds before its
first create_skill call, whose specification was 13,958 characters. Argument
streaming had previously been invisible. The builder drafted Python in reasoning
before create_action; this was extra text generation, not a second persisted
implementation. A subsequent rebuild followed a real KeyError for `lang` in log
formatting. Builder instructions now request the action plan before implementation
and reserve full scripts for create_action.source.

Validation: 70 PostgreSQL tests, 13 frontend replay/projection tests, TypeScript and
Vite production build. A real OpenRouter MiniMax M3 run in an isolated PostgreSQL
schema created and activated saudacao_local. The workbench recorded its own
reasoning plus 678 child stream fragments; browser inspection captured live child
activity. Only one code version was created. The model still drafted a small
Python block in reasoning, so redundant drafting is not claimed to be eliminated.
Evidence: `.runtime/live-validation/3dbfd6895878/activity-report.json` and
`.runtime/ui-validation/live-child-stream.png`. Scripted adapter tests cover
deterministic event behavior; they are separate from this real-model probe.

Follow-up corrections: reasoning nodes no longer inherit the chat disclosure's
left margin. Parallel branches wrap into a grid within the timeline column.
Activity markers wrap left-to-right, put each tool icon on its own line and omit
tool names from that strip. The 4,000-character recent tail scrolls to follow new
activity; manual scrolling upward exposes a Latest activity button. Elapsed time
is separate from the explicitly labelled age of the last activity, and the empty
stream placeholder uses compact text. Job IDs appear beside execution statuses;
tool cards show the event ID, with full job/call/event IDs in expanded details.

Large inspect_execution results used to truncate the entire lifecycle payload,
discarding tool_call_id and tool_name. The UI therefore rendered an unmatched
running card and a nameless completed card. Tool event persistence now bounds the
body while preserving identity, concurrency metadata and error verdicts. Reading
legacy truncated events recovers only complete top-level identity fields before
the body; it never matches IDs inside nested inspection results and does not
rewrite stored events. Browser verification confirmed single completed cards for
production events 13704 and 14646, a 307px timeline with 307px scroll width, wrapped
markers and activity scroll positioned at its bottom. Evidence:
`.runtime/ui-validation/activity-wrap-and-event-ids.png`.
Updated validation: 73 PostgreSQL tests, 13 frontend tests, TypeScript/Vite build.

## Provider usage and continuous activity (2026-10-07)

Usage callbacks now persist provider-reported input/output/cache tokens and USD
cost per model request, linked to model_call_id on streamed blocks and tool
lifecycle events. /api/usage aggregates requests once by provider/request ID and
attributes descendant usage to parent executions without double-counting the
instance total. Techdoc and asynchronous memory extraction also capture usage.
OpenRouter's reported cost is used directly; there are no estimated prices.
Missing amounts display as unavailable or a partial lower bound, and historical
executions without captured usage are not assigned fabricated costs. A block's
tooltip explains that other blocks from the same model request share that usage;
their repeated badges must not be summed. The header shows the recorded instance
total. Build cards show their execution plus descendants.

Activity is always collapsible, including while running. Tool glyphs stay inline
with the fragment markers. CSS allows breaks between any indicators, filling the
available width rather than treating runs around an emoji as unbreakable words.
OpenAI-wire argument fragments are counted before call-ID-dependent translation
can buffer them, and are not counted again when translated JSON arrives. When
the provider sends no fragments for a while, the status explicitly says it is
waiting for the next provider fragment. A real MiniMax probe still delivered
create_action arguments as one complete wire fragment; the UI does not invent
incremental source streaming when the provider does not send it.

Routine prose repairs literal non-ASCII Unicode escapes on read, write and
execution, including surrogate pairs; ASCII/control escapes and code are not
globally decoded. The existing Rolar 2d50 routine was verified in the browser
with correct Portuguese accents. The sidebar now uses the bare symbol and
wordmark, optically aligned, without the AGENT ENGINE subtitle; chat avatars
retain the rounded blue background. Tool statuses appear at the far right,
after usage and debug IDs.

Validation: 76 PostgreSQL tests, 13 frontend tests and production build. Real
MiniMax M3 probe 88e5b5d151d2 completed and activated its test action, reported
14 requests / 135,549 tokens / USD 0.02127992, and delivered 381 child fragments
to the workbench. This uses a separate schema and workspace. Evidence:
`.runtime/live-validation/88e5b5d151d2/activity-report.json`,
`.runtime/ui-validation/usage-status-and-brand.png`, and
`.runtime/ui-validation/routine-unicode-and-brand.png`.

## Routine schedule editor

Routine editing supports manual, minute intervals, hourly, daily, weekly, and monthly schedules. The visual controls translate to cron and recognize equivalent common expressions; advanced expressions are preserved verbatim. A read-only preview computes the next three occurrences with the scheduler's timezone rules. Changes, including weekday and enable checkboxes, remain drafts until Save routine; Cancel discards them. Verified in the browser by changing weekdays and enabled state, cancelling, and reopening the original manual routine. Frontend round-trip tests and PostgreSQL preview tests cover custom expressions, invalid schedules, timezones, month boundaries, and DST. The model header displays only the dollar amount, with $0.00 when unavailable.

The workbench's `01 > Agent console` session heading uses the vendored Scificn Breadcrumb, alongside the toolbar breadcrumb. Explicit parallel batches render inside a Scificn Card containing branch cards and a join footer; failures propagate to the enclosing batch status. Per-call version IDs appear in both chat tool cards and the graph. Rendering comes from durable batch/tool events, so reopening the session retains grouping. Sequential historical batches are not relabelled parallel. Verified in the browser against the real MiniMax probe; no horizontal overflow in the graph or batch card. Regression checks: 20 frontend tests, production build, 26 focused PostgreSQL backend tests.

## Long conversation rendering

The conversation and execution sequence start with the latest 30 blocks. Scrolling upward within 160 px of the loaded top prepends another 30; scrolling toward the loaded bottom restores newer blocks when needed. The opposite edge is evicted in whole pages to keep at most 90 blocks mounted. The visible row and its pixel offset are retained across page changes. Ordinary scrolling within a page does not mount/unmount rows or restart loaders. Completed build progress is cached (up to 100 jobs) so returning to it does not restart polling.

Graph links can bring an unmounted message into the window; Latest returns to the most recent page. Pending human/Vault forms remain mounted outside the paged history. This only changes rendering, not database history or model context. Raw events still arrive through the existing stream and remain in browser memory; server-side history pagination is separate work.

Verified against an existing 102-block conversation and 107-node sequence: initial pages contain 30 blocks, crossing the upper threshold grows to 60 and 90, and navigating farther back evicts the opposite edge without exceeding 90. Small scrolls in the middle do not change the page. Measured the same graph card before/after prepending: it moved only by the requested wheel distance. Frontend regression tests cover thresholds, direction, overlap and traversing 10,000 items in both directions.
