# File and shell tools

Fluxyr exposes four general-purpose operations to both the workbench and the
skill builder, using the Pi tool contracts. Domain tools for skills, memory,
vault, routines, human interaction, web research and previews remain available.
The old `read_file`, `write_file`, `list_files`, `search_files` and `mutate_file`
model tools have been removed rather than aliased.

| Tool | Input | Behavior |
| --- | --- | --- |
| `read` | `path`, optional `offset` / `limit` | UTF-8 text, 1-indexed lines. At most 2,000 lines / 50 KiB, with the next offset. Images become actual model image attachments. |
| `write` | `path`, `content` | Creates parent directories and atomically replaces the file. Returns a short confirmation, not a copy of its contents. |
| `edit` | `path`, `edits: [{oldText, newText}]` | Matches all replacements against the original; rejects missing, ambiguous, overlapping or no-op edits before writing. Returns a unified diff. |
| `bash` | `command`, optional `timeout` (seconds) | Fresh Bash process, combined stdout/stderr streaming, exit code and bounded tail. Use standard shell commands for discovery, search, directories, moves and deletion. |

`read`, `write`, `edit` and `bash` share the persistent data directory as cwd.
For example, `read(path="sales.csv")`, `bash(command="cat sales.csv")` and an
action's `data_dir / "sales.csv"` address the same file. Do not prefix `data/`
to relative paths. Absolute paths and `~` are supported; `~` refers to the server
user's home. Each Bash invocation starts fresh: `cd` and `export` do not carry
over to the next call. Deployment environment variables are inherited.

`edit` preserves BOM and CRLF line endings. It accepts minor smart-quote, dash,
Unicode compatibility and trailing-space differences when exact matching fails;
untouched lines retain their text. Multiple mutations through the file tools or
the file-browser editor serialize per resolved path. Arbitrary shell programs
and actions have normal filesystem concurrency semantics, so avoid simultaneous
writes to the same file through those paths.

The model receives PNG, JPEG, GIF, WebP and BMP images as JPEG attachments,
resized to fit 2,000 × 2,000 pixels and 3 MiB. Animated formats use their first
frame. Input is limited to 20 MiB and 25 million pixels. The selected provider
and model must support image input. OpenAI/OpenRouter and Anthropic adapters
both transport the image itself, not base64 text in the prompt. Older tool images
are omitted once their encoded payloads would exceed 12 MiB of active context;
the model can read a file again. Memory extraction and UI events omit binary data.

## Process lifecycle and protection

- `FLUXYR_TOOL_TIMEOUT` sets the default and maximum Bash timeout (300 seconds).
- The model sees the last 2,000 lines / 50 KiB. Larger output is stored under
  `workspace/<job>/tools/<id>.log`; the response includes an absolute path that
  `read` can paginate. `FLUXYR_BASH_MAX_OUTPUT_BYTES` caps this spool at 32 MiB by
  default. Exceeding it terminates the process and reports an error.
- Pause suspends the process group; resume continues it. Paused time does not
  consume the timeout. Cancel and timeout terminate the process group.
- Background descendants are scoped to the tool call; Bash is not a service
  supervisor. Output progress is throttled, and the runner waits for pipe
  readiness instead of busy-looping.
- Mutating tools use the engine's durable effect ledger. A completed call is
  not rerun after resume; an uncertain prior dispatch is not silently repeated.
- `FLUXYR_EXECUTION_MODE=landlock` applies the same Linux write restrictions to
  all four tools, allowing changes only in `data/` and this job's `tools/`
  directory. Reads remain governed by OS permissions. `local` uses ordinary OS
  permissions. See [execution protection](EXECUTION_PROTECTION.md).
- The deployment must include Bash and whichever CLI commands the application
  needs. The supplied Debian-based images include Bash and standard filesystem
  utilities. Optional programs such as `rg` must be installed by the deployer.

## Storage and existing helpers

The private HTTP file-browser service remains limited to `data/`, with ETag
conflict checking for manual edits. Uploads, previews and tool operations access
the same files; previews accept data-relative paths or absolute paths contained
within `data/`. There is no remote filespace, S3 migration or second copy.

Actions remain versioned source in PostgreSQL. The Python runner copies source
and `fluxyr.py` into a disposable invocation directory. That local helper still
provides `params`, `data_dir`, `output`, Vault access and human continuation;
these contracts are unchanged. Writing a `.py` file with general tools does not
register an action: the builder still calls `create_action`, and the workbench
then tests/activates its version.

Workspace retention also covers Bash output and scratch files. Pending jobs are
kept; old terminal-job directories are purged at startup according to
`FLUXYR_WORKSPACE_RETENTION_DAYS`. Durable action source, history and user data
are not purged.

## Verification

`tests/test_workspace_tools.py` executes actual file/shell processes and action
helpers, checks both provider image payloads, and runs the same filesystem cases
in local and Landlock modes. Linux CI can set `FLUXYR_TEST_REQUIRE_LANDLOCK=1` to
fail instead of skipping unsupported protection.

`scripts/validate_live_workspace.py --run` uses the real configured OpenRouter
credential and MiniMax M3 against an isolated PostgreSQL schema and data root.
It supplies a natural file-processing request and random CSV values, without
supplying implementation code or prescribing a tool sequence. It checks the
resulting files and retains tool-event evidence under `.runtime/live-validation/`.
