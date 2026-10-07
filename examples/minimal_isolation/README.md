# Minimal Linux application with protected action writes

`app.py` starts the HTTP app and embedded worker. The same container executes
Python skills with Landlock; no Piston, isolate service or privileged container.
The image defaults to `FLUXYR_EXECUTION_MODE=landlock` and fails initialization
if the deployment cannot enforce it. See [execution protection](../../docs/EXECUTION_PROTECTION.md)
for requirements and boundaries.

## Prepare the package

Fluxyr Agent is not assumed to be published on PyPI. From the repository root,
build its UI and wheel, then copy the wheel into this example:

```sh
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
python -m build --wheel
mkdir -p examples/minimal_isolation/vendor
cp dist/fluxyr-0.1.0-py3-none-any.whl examples/minimal_isolation/vendor/
cd examples/minimal_isolation
docker build -t fluxyr-minimal-isolation .
cp .env.example .env
```

Set `DATABASE_URL`, `OPENROUTER_API_KEY` and `VAULT_ENCRYPTION_KEY` in `.env`.
Generate the vault key once, keep it in deployment secrets and preserve it across
restarts. If using an existing agent database, use its existing vault key. Only
`data/` is persisted in this example; the automatically generated fallback key
under `.runtime/` would otherwise be lost when the container is replaced.

```sh
docker run --rm --name fluxyr-minimal-isolation \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --env-file .env \
  -p 127.0.0.1:8080:8080 \
  -v fluxyr-minimal-data:/var/lib/fluxyr/data \
  fluxyr-minimal-isolation
```

PostgreSQL is external. `data/` holds durable files/previews; workspace copies and
cached dependency environments are disposable container files. Database records
retain source versions, execution history and human-interaction continuation.
Pass normal deployment variables to the container; actions inherit them.

## Shpyrd

The `shpyrd.yaml` follows the Dockerfile example: one web process with its own
embedded worker and a single data volume. Use the project/secret configuration
workflow to set the same environment variables, create the `data` volume and
deploy this directory (including the prepared wheel). No capability additions
or privileged pod are required. Shpyrd's injected `PORT` takes precedence.

The actual Shpyrd node must support Landlock ABI >= 3 and permit its syscalls in
the runtime seccomp profile. The app checks this at startup; this example does
not change the cluster or its security configuration.

For native macOS development, use `FLUXYR_EXECUTION_MODE=local python app.py`.
That explicit mode runs with the process's normal filesystem permissions.
