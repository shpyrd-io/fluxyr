# Python execution protection

Set `FLUXYR_EXECUTION_MODE=landlock` on a Linux deployment to prevent accidental
filesystem writes outside each action or file/shell tool’s allowed directories. The default `local`
mode remains available for local development, including macOS. The same package
and Dockerfile support both modes; no remote executor, privileged container,
sidecar, extra daemon, or Python dependency is required.

## Policy

| Path | During an action |
| --- | --- |
| `FLUXYR_ROOT/data/` | Read, write, create, delete |
| `FLUXYR_ROOT/workspace/<job>/<invocation>/` | Read, write, create, delete |
| Engine, dependencies, sibling workspaces and other paths | Read/execute subject to OS permissions; file writes, creation, removal, rename and truncation denied |
| `/dev/null` | Writable for ordinary subprocess redirection |

Paths are not hidden or remapped. Relative and absolute paths, symlinks and native
subprocesses are subject to the same kernel policy. The parent engine retains its
normal permissions. The shared `data/` directory is writable across actions;
Landlock does not protect it from an action that intentionally deletes its contents.

All process environment variables are inherited, including database/API settings.
`PATH` is prefixed with the action's virtualenv; `HOME`, `TMPDIR`, `TMP`, `TEMP`
and `XDG_CACHE_HOME` point into the invocation folder. `FLUXYR_DATA` points to
the persistent data directory. Vault secrets remain passed through stdin and
redacted from results/logs. Arbitrary inherited environment values are not
automatically redacted: do not print credentials.

The launcher uses only the standard library and Linux syscalls. It starts in a
fresh process with Python site hooks disabled, applies `no_new_privs` and Landlock,
then replaces itself with the action's Python interpreter. No `preexec_fn` runs
inside the multithreaded server. Unneeded file descriptors are closed by Popen;
stdin/stdout/stderr remain the engine's pipes. Streaming, timeout, process-group
pause/cancel and human-interaction continuation use the existing runner protocol.

Dependency preparation uses the same restriction, allowing writes only within
the selected cached virtualenv (including its disposable setup directory), not
`data/`. During action execution, that environment is read-only under the policy.
Declare required packages in the action's dependencies; runtime `pip install`
into the cached environment will fail. Installation still requires normal
network/package-index access.

## Requirements and scope

Linux x86_64, aarch64 or riscv64 with Landlock enabled, ABI **3 or newer**. ABI 3
is required to protect against truncation as well as ordinary writes. On ABI 5+
device ioctls are additionally restricted. Startup probes rule creation and
enforcement in a disposable child, and every action reapplies the policy. If
the host or its seccomp profile denies a syscall, startup/execution fails with
an explicit error; there is no silent fallback to `local`.

This is filesystem damage prevention for trusted installation code, not a
hostile-code boundary. Network/database access and signals are not isolated.
Landlock does not cover all filesystem metadata changes (such as chmod), and
pre-existing hard links into writable directories can alias files elsewhere.
Use a non-root user, root-owned application/runtime files and normal file
permissions as the baseline; avoid hard links from protected files into data.
Native `@app.tool` functions and HTTP handlers execute inside the engine and
are outside the generated-action runner's policy.

Docker/Compose: set `FLUXYR_EXECUTION_MODE=landlock` in the deployment environment.
No `--privileged`, added capabilities, or `seccomp=unconfined` is necessary on a
compatible host. On Shpyrd, configure the same variable and inherited application
environment normally. Shpyrd's non-root/capability-drop policy is compatible in
principle, but the actual node kernel and runtime seccomp profile must pass the
startup probe; Docker Desktop validation does not certify a Shpyrd cluster.

Kernel reference: [Landlock documentation](https://docs.kernel.org/userspace-api/landlock.html).

## Validation

Run the real subprocess tests on a compatible Linux host:

```sh
FLUXYR_TEST_REQUIRE_LANDLOCK=1 python -m pytest tests/test_execution_protection.py -q
```

The flag makes missing kernel support a test failure, not a skip. On macOS,
ordinary test runs skip Linux-only cases while still exercising local mode.
The tests use same-owner writable sentinel files, so the negative checks do not
depend on a read-only Docker bind mount or Unix ownership blocking the operation.

Validated on Docker Desktop's Linux 7.0.14 kernel, Landlock ABI 8, Python 3.12,
UID 10001, `--cap-drop ALL`, `--security-opt no-new-privileges=true`, and default
seccomp. The protection suite plus existing local-tool, human-continuation and
parallel-action regressions passed **45 tests**; one existing optional online
dependency-download test was skipped. A separate offline wheel test performed
real pip installation under the restricted dependency setup policy.

A seccomp profile deliberately denying `landlock_restrict_self` also verified
that initialization fails explicitly. The installed-wheel consumer Docker
example successfully wrote data and failed an attempted deletion of its app.
The framework/configuration/retention regressions passed **48 tests** against
disposable PostgreSQL schemas. These checks exercise real Python processes and
kernel enforcement; they do not call or simulate an LLM.

The ready-to-build consumer example is [minimal_isolation](../examples/minimal_isolation/README.md).

The `read`, `write`, `edit` and `bash` tools use this same launcher. Their writable roots are `data/` and `workspace/<job>/tools/`. File operations execute in short-lived child processes so the engine itself stays unrestricted. `~` in these tools refers to the original server home (unlike action HOME); temporary/cache paths still point inside the allowed workspace. See [file tools](FILE_TOOLS.md).
