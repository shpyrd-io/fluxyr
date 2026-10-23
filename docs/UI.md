# Fluxyr interface

The interface uses Tailwind v4, Radix behavior primitives and a small, local
component library in `frontend/src/ui`. Its neutral surfaces, system typography,
compact navigation and soft controls follow the Calm design direction. Chat is
the primary surface, with white/neutral content, a quiet gray sidebar, and an
execution inspector opened only when requested.
The Fluxyr brand accent comes from `fluxyr.com` (`#4a3ec7`); it is reserved for
primary actions, links, selection and focus. The original component ancestry is
recorded in NOTICE and `frontend/src/ui/scificn-source.json`.

## Where to change the design

| File | Responsibility |
| --- | --- |
| `frontend/src/styles/themes.css` | Shared semantic colors, radii, control height, sidebar width and page spacing; light/dark palettes. |
| `frontend/src/styles/base.css` | Tailwind entrypoint, font families, reset, animations and reduced-motion behavior. |
| `frontend/src/styles/components.css` | Appearance of reusable buttons, inputs, textareas, selects, labels, cards, panels, dialogs, badges, switches, checkboxes, breadcrumbs and runtime indicators. |
| `frontend/src/catalogue.tsx` | Shared search, row identity and empty-state composition for resource lists. |
| `frontend/src/ui/` | React component structure and accessible Radix behavior. Keep visual decisions in the shared CSS. |
| `frontend/src/styles/workspace.css` | Calm app shell, session toolbar, conversation, composer, execution inspector and their responsive layouts. |
| `frontend/src/style.css` | Resource catalogues, forms, file previews and supporting screen composition. |

Import a shared primitive rather than recreating a control inside a page. Use
semantic variables instead of literal colors or per-page theme overrides. The
existing Button/Badge variant names remain compatible with application callers.
Code, paths, IDs and payloads retain a monospace font; normal interface and
conversation text use the platform sans-serif stack. No hosted font service is
required.

## Appearance

Use the theme icon at the right of the header, beside the system status, to choose
**Light**, **Dark** or **System**. System is the default and follows the operating
system's appearance, including changes while the page is open. The preference is
saved in this browser and shared across tabs; no server setting is required.

Both themes cover chat, execution sequence, forms, popovers, dialogs and navigation.
The initial theme is resolved before React renders. The icon-only session status
retains accessible text, and the compact session toolbar keeps the session ID and
actions without a duplicate heading. Reduced-motion preference disables animation.

The opt-in `tests/test_theme_ui.py` browser test checks system changes, explicit
overrides, persistence after reload, keyboard selection and the mobile control.

## Calm workspace

The internal Workbench route is labeled **Chat** in navigation; API/session names
are unchanged. The header shows the configured agent name, current page, total
cost, worker health and theme selector. Model information sits inside the composer.
Session status, diagnostics, memory and clear-context controls remain available
in a compact toolbar with labeled icon buttons and keyboard focus indicators.

**Show execution sequence** opens the inspector; it starts closed and unmounts
when dismissed so hidden graph nodes do not keep rendering. Its close button or
Escape restores focus to the toolbar. On narrow screens the inspector uses the
conversation area; closing it or selecting an event returns to the chat. Existing
history windowing and scroll-following continue across layout changes. Closing
the inspector does not interrupt execution, SSE updates or persistence.

Tool summaries retain their names, result status and reported usage. Opening one
still loads its payload lazily, including job/call/event/version identifiers.
Approvals, Vault forms, file previews, builder progress, pause/resume/cancel and
queued messages retain their existing behavior. Human requests use neutral
surfaces; semantic status and error colors remain distinct. Only the composer
container receives the focus treatment, avoiding a second ring around its textarea.

## Component consistency

The execution inspector uses Card/Header/Title/Content for persisted nodes,
preserving parallel groups, status, usage, version IDs and navigation back to
the conversation. Forms use shared
Radix Select, Checkbox, Switch and Dialog components, with labels and field IDs.
Dialogs keep focus management and Escape dismissal; changing a form field never
saves it automatically. Switches have a visible thumb and checkboxes a checkmark.

Execution/tool failure badges use the shared Status component: `error` and
`failed` both display **Failed** in red; `succeeded` and `completed` display
**Completed**. Backend state values remain unchanged. Runtime stdout and dependency
installation events remain available under **Diagnostics**, with execution and
call IDs, rather than becoming conversation entries.

Tools, Executions and Files follow the same list hierarchy as Skills, Routines
and Vault: neutral surfaces, an icon and title, secondary metadata, and controls
aligned on the right. Shared catalogue components keep search and row identity
consistent. Tools retain category filtering, parameter tables and full JSON
schemas. Executions offer search and status filtering over the loaded jobs and
open the original conversation. Files use the full width until an editor or
preview opens, preserving upload, download, editing and deletion confirmation.
Categories and filters do not change availability or permissions.
Streaming text uses a CSS-animated loading icon, removed when the stream closes;
it does not schedule React renders for animation frames.

Live SSE connections subscribe to in-process notifications for their session.
After an event transaction commits, waiting connections read the next durable
events immediately; they do not hold a database connection while waiting. A
10-second timeout provides heartbeats and catches writes from other processes.
Immediate notifications apply to the default embedded worker and HTTP server
sharing the same Database instance. No extra service is required.

The UI also opts into `GET /api/events?...&text=live`. This sends named `fragment`
SSE events directly from provider callbacks, without waiting for a database write
or introducing a transmission timer. These events have **no SSE id** and never
advance `Last-Event-ID`. Each carries `job_id`, `type` (`delta` or `reasoning`),
and a payload with `block_id`, `text`, `text_offset`, and `text_end`. Offsets use
UTF-16 code units to match browser string indexing, including emoji. Persisted
text events retain the same offsets; the client displays only the live suffix
beyond the saved text. Old records without offsets and clients without this
opt-in retain durable-only replay.

Live fragments update only the active message at animation-frame cadence;
history and the execution graph continue to use batched durable events. Finished
Markdown messages are memoized. Reconnection discards speculative tails and
replays from the durable cursor. Missing prefixes (joining mid-stream or queue
overflow) wait for persistence rather than displaying text across a gap.

Persistence remains independent of viewers: ActivityEmitter batches adjacent
fragments nominally every 250 ms as provider events arrive and flushes at block
boundaries and execution settlement. Scheduled, reactive and worker routines
save their text even with no browser open. Opening an execution later loads its
saved history; live content is scoped to its own session, without exposing child
text in the main conversation. There is no background persistence writer tied to
the lifetime of an HTTP connection.

Each connected reader has a bounded 128 KiB live queue, released on disconnect.
Slow readers may miss speculative fragments but recover saved content. The UI
bounds live tails to 128 blocks of 65,536 UTF-16 units each, discards tails when
blocks/executions finish, and stops scheduled frames on unmount. With no viewers,
no live queue is retained. A process crash can lose the uncommitted suffix that
was already visible; interrupted executions fall back to the durable history.

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

The main toolbar uses the shared Breadcrumb; the session toolbar shows only session identity, status and actions. Explicit parallel batches render inside a shared Card containing branch cards and a join footer; failures propagate to the enclosing batch status. Per-call version IDs appear in both chat tool cards and the graph. Rendering comes from durable batch/tool events, so reopening the session retains grouping. Sequential historical batches are not relabelled parallel. Verified in the browser against the real MiniMax probe; no horizontal overflow in the graph or batch card. Regression checks: 20 frontend tests, production build, 26 focused PostgreSQL backend tests.

## Long conversation rendering

The conversation and execution sequence start with the latest 30 blocks. Scrolling upward within 160 px of the loaded top prepends another 30; scrolling toward the loaded bottom restores newer blocks when needed. The opposite edge is evicted in whole pages to keep at most 90 blocks mounted. The visible row and its pixel offset are retained across page changes. Ordinary scrolling within a page does not mount/unmount rows or restart loaders. Completed build progress is cached (up to 100 jobs) so returning to it does not restart polling.

Graph links can bring an unmounted message into the window; Latest returns to the most recent page. Pending human/Vault forms remain mounted outside the paged history. This only changes rendering, not database history or model context. As of 0.9.9, server-side pagination also bounds the initial download (see below).

Verified against an existing 102-block conversation and 107-node sequence: initial pages contain 30 blocks, crossing the upper threshold grows to 60 and 90, and navigating farther back evicts the opposite edge without exceeding 90. Small scrolls in the middle do not change the page. Measured the same graph card before/after prepending: it moved only by the requested wheel distance. Frontend regression tests cover thresholds, direction, overlap and traversing 10,000 items in both directions.


## Paged history and live updates (0.9.9)

The chat and execution sequence bootstrap with
`GET /api/sessions/<id>/history`. Each page reads at most 1,200 relevant persisted
records and consolidates text/reasoning fragments by block. Internal activity and
usage bookkeeping are omitted from historical pages. The response contains
`events`, boundary-overlapping `messages`, `event_cursor`, an optional `before`
cursor, and the event interval `(range_start, range_end]` represented by the page.

Older pages are requested with `?before=<cursor>` only when the user scrolls
up to the oldest loaded edge of the chat or execution sequence. They preserve the
visible scroll anchor. The existing 30-item/90-item DOM window remains in place;
only requested history is retained in browser memory.

After bootstrap, the UI opens
`/api/events?session_id=<id>&activity=live&view=chat&after=<event_cursor>`.
The cursor bridges the snapshot and live stream, including events committed
while the snapshot was in flight. Native SSE reconnection uses `Last-Event-ID`.
Periodic and lifecycle-triggered reconciliation uses `history?after=<cursor>`;
`has_more` signals additional bounded catch-up pages. Clients replace only the
returned event interval, retain newer concurrent events, and deduplicate messages
by ID. A `reset` response replaces local history after a context archive/reset.

Tool cards carry status, IDs and a `details_url`; opening a card fetches the full
persisted event from `GET /api/events/<id>`. Execution reports carry only navigation
metadata. Stored events, action results and model context are not modified.
The original full event API remains available to existing clients.

Usage totals are reconstructed once per server process, then updated from newly
persisted usage events. Provider request IDs remain deduplicated across updates,
and descendant usage still contributes to parent totals. JSON responses use
UTF-8 directly. This reduces transfer without depending on proxy compression.
