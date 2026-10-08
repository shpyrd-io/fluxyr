import { useCallback, useEffect, useState } from "react";
import { api, date, type RecordData } from "./api";
import { Button, Input, Textarea, Badge } from "./components";
import { Label } from "./ui/label/label";
import { Separator } from "./ui/separator/separator";
import { SelectField, SelectOption } from "./form-select";

const example = `from fluxyr import params, output

# Adapt this to real collected payloads; unknown formats must fail visibly.
body = params["json"]
if body.get("type") == "presence":
    output({"events": [], "ignored_reason": "Presence update"})
elif body.get("type") == "message":
    output({"events": [{
        "session_key": body["sender"],
        "event_id": body["id"],
        "payload": body
    }]})
else:
    raise ValueError("Unsupported event type")
`;

export function ReactiveRoutine({
  routine,
  reload,
  open,
}: {
  routine: RecordData;
  reload: () => Promise<any>;
  open: (id: string) => void;
}) {
  const root = `/routines/${routine.id}`;
  const config = routine.reactive;
  const [mode, setMode] = useState(config.mode);
  useEffect(() => {
    setMode(config.mode);
  }, [config.mode]);
  const [version, setVersion] = useState(config.normalizer_id || "none");
  const [versions, setVersions] = useState<RecordData[]>([]);
  const [receipts, setReceipts] = useState<RecordData[]>([]);
  const [before, setBefore] = useState<number | null>(null);
  const [selected, setSelected] = useState<RecordData | null>(null);
  const [sessions, setSessions] = useState<RecordData[]>([]);
  const [sessionAfter, setSessionAfter] = useState<string | null>(null);
  const [result, setResult] = useState<RecordData | null>(null);
  const [source, setSource] = useState(example);
  const [dependencies, setDependencies] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const [page, versions] = await Promise.all([
      api(root + "/receipts"),
      api(root + "/normalizers"),
    ]);
    setReceipts(page.items);
    setBefore(page.next_before);
    setVersions(versions);
    setLoaded(true);
  }, [root]);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);
  async function act(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const url = new URL(config.webhook_path, window.location.origin).href;
  return (
    <section className="reactive-inbox">
      {routine.trigger !== "worker" && (
        <Label>
          POST webhook URL
          <Input value={url} readOnly onFocus={(e) => e.target.select()} />
        </Label>
      )}
      <p className="muted">
        Collect examples first. Testing does not create conversations.
        Activating processes new events only; old samples require explicit
        replay.
      </p>
      <div className="reactive-settings">
        {routine.trigger !== "worker" && (
          <Label>
            Mode
            <SelectField value={mode} onValueChange={setMode}>
              <SelectOption value="collecting">
                Collecting examples
              </SelectOption>
              <SelectOption value="active">Active</SelectOption>
              <SelectOption value="disabled">Disabled</SelectOption>
            </SelectField>
          </Label>
        )}
        <Label>
          Normalizer version
          <SelectField
            value={version}
            onValueChange={(v) => {
              setVersion(v);
              setResult(null);
            }}
          >
            <SelectOption value="none">No normalizer</SelectOption>
            {versions.map((v) => (
              <SelectOption key={v.id} value={v.id}>
                {v.id.slice(0, 8)} · {date(v.created_at)}
              </SelectOption>
            ))}
          </SelectField>
        </Label>
      </div>
      {routine.trigger !== "worker" && (
        <details>
          <summary>Webhook authentication</summary>
          <p>
            The URL token authorizes delivery.{" "}
            {config.signature_enabled
              ? "HMAC verification is also enabled."
              : "Optional HMAC verification is off."}{" "}
            Keep this URL private.
          </p>
          <Label>
            New HMAC signing secret
            <Input
              type="password"
              autoComplete="new-password"
              value={signingSecret}
              onChange={(e) => setSigningSecret(e.target.value)}
              placeholder="At least 16 characters; leave blank to keep current setting"
            />
          </Label>
          <small>
            Send X-Webhook-Signature with the hex HMAC-SHA256 of the exact
            request body. Configure provider-specific signature formats in a
            custom route.
          </small>
        </details>
      )}
      <Button
        disabled={busy}
        onClick={() =>
          act(async () => {
            await api(root + "/reactive", "PATCH", {
              ...(routine.trigger === "worker" ? {} : { mode }),
              normalizer_id: version === "none" ? null : version,
              ...(signingSecret ? { signing_secret: signingSecret } : {}),
            });
            setSigningSecret("");
            await reload();
            await load();
          })
        }
      >
        {routine.trigger === "worker"
          ? "Save normalizer selection"
          : "Save reactive settings"}
      </Button>
      <Separator />
      <div className="actions">
        <strong>Received events</strong>
        <Button
          disabled={busy}
          onClick={() =>
            act(async () => {
              await load();
              await reload();
            })
          }
        >
          Latest receipts
        </Button>
        <small>
          {Object.values(config.counts || {}).reduce(
            (a: number, b) => a + Number(b),
            0,
          )}{" "}
          receipts retained · showing {receipts.length} (20 per page)
        </small>
      </div>
      {!loaded && <p className="muted">Loading received events…</p>}
      {loaded && !receipts.length && (
        <p className="muted">
          {routine.trigger === "worker"
            ? "Waiting for listener events. Start collection from the controls above."
            : "Waiting for the first POST. Send JSON, text or URL-encoded form fields to the webhook URL above."}
        </p>
      )}
      <div className="reactive-receipts">
        {receipts.map((r) => (
          <Button
            key={r.id}
            className="reactive-receipt"
            disabled={busy}
            onClick={() =>
              act(async () => {
                setSelected(await api(`${root}/receipts/${r.id}`));
                setResult(null);
              })
            }
          >
            <code>#{r.id}</code>
            <span>{date(r.created_at)}</span>
            <Badge
              variant={
                r.status === "failed"
                  ? "CRITICAL"
                  : r.status === "processing" || r.status === "pending"
                    ? "SCANNING"
                    : "OFFLINE"
              }
            >
              {r.status}
            </Badge>
          </Button>
        ))}
      </div>
      {before && (
        <Button
          disabled={busy}
          onClick={() =>
            act(async () => {
              const page = await api(`${root}/receipts?before=${before}`);
              setReceipts(page.items);
              setSelected(null);
              setResult(null);
              setBefore(page.next_before);
            })
          }
        >
          Older receipts · next page
        </Button>
      )}
      {selected && (
        <div className="reactive-sample">
          <div className="actions">
            <strong>Receipt #{selected.id}</strong>
            <span>{selected.status}</span>
            <Button onClick={() => setSelected(null)}>Close</Button>
          </div>
          <pre>{JSON.stringify(selected.envelope, null, 2)}</pre>
          {selected.error && (
            <p role="alert" className="form-error">
              {selected.error}
            </p>
          )}
          <div className="actions">
            <Button
              disabled={busy || version === "none"}
              onClick={() =>
                act(async () => {
                  setResult(
                    await api(`${root}/normalizers/${version}/test`, "POST", {
                      receipt_id: selected.id,
                    }),
                  );
                  setSelected(await api(`${root}/receipts/${selected.id}`));
                  await load();
                })
              }
            >
              Test selected version · no dispatch
            </Button>
            <Button
              disabled={
                busy ||
                config.mode !== "active" ||
                !["collected", "failed", "ignored"].includes(selected.status)
              }
              onClick={() =>
                act(async () => {
                  await api(
                    `${root}/receipts/${selected.id}/replay`,
                    "POST",
                    {},
                  );
                  setSelected(await api(`${root}/receipts/${selected.id}`));
                  await load();
                  await reload();
                })
              }
            >
              Replay into agent
            </Button>
            <Button
              className="danger"
              disabled={
                busy || ["pending", "processing"].includes(selected.status)
              }
              onClick={() =>
                act(async () => {
                  await api(`${root}/receipts/${selected.id}`, "DELETE");
                  setSelected(null);
                  await load();
                  await reload();
                })
              }
            >
              Delete receipt
            </Button>
          </div>
          {result && (
            <>
              <strong>Test: {result.status}</strong>
              <pre>
                {JSON.stringify(
                  result.result || { error: result.error },
                  null,
                  2,
                )}
              </pre>
            </>
          )}
          {selected.result && (
            <details>
              <summary>Routing result</summary>
              <pre>{JSON.stringify(selected.result, null, 2)}</pre>
            </details>
          )}
          {(selected.result?.deliveries || []).map(
            (d: RecordData, i: number) => (
              <Button key={i} onClick={() => open(d.session_id)}>
                Execution {d.job_id.slice(0, 8)} · {d.execution_status}
                {d.duplicate ? " · duplicate delivery" : ""} ↗
              </Button>
            ),
          )}
          {!!selected.attempts?.length && (
            <details>
              <summary>
                Normalization attempts ({selected.attempts.length})
              </summary>
              <pre>{JSON.stringify(selected.attempts, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
      <details>
        <summary>Conversations</summary>
        <p>
          Each identity has its own context. Resetting an idle conversation
          preserves its history and starts fresh on the next event.
        </p>
        <Button
          disabled={busy}
          onClick={() =>
            act(async () => {
              const page = await api(root + "/sessions");
              setSessions(page.items);
              setSessionAfter(page.next_after);
            })
          }
        >
          Load conversations
        </Button>
        {sessions.map((s) => (
          <div className="actions" key={s.session_id}>
            <Button onClick={() => open(s.session_id)}>
              {s.session_key} · {s.status} ↗
            </Button>
            <Button
              disabled={busy || s.status !== "idle"}
              onClick={() =>
                act(async () => {
                  await api(root + "/sessions/reset", "POST", {
                    session_key: s.session_key,
                  });
                  setSessions((old) =>
                    old.filter((x) => x.session_id !== s.session_id),
                  );
                })
              }
            >
              Reset context
            </Button>
          </div>
        ))}
        {sessionAfter && (
          <Button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const page = await api(
                  root + "/sessions?after=" + encodeURIComponent(sessionAfter),
                );
                setSessions((old) => [...old, ...page.items]);
                setSessionAfter(page.next_after);
              })
            }
          >
            More conversations
          </Button>
        )}
      </details>
      <details>
        <summary>Create a normalizer version</summary>
        <p>
          Python runs before any session exists. Use params["json"],
          params["form"], params["text"] and params["query"]. Form and query
          values are arrays. Emit events with session_key and payload; event_id
          deduplicates retries. Return an empty events array with ignored_reason
          to filter noise. No network calls or side effects.
        </p>
        <Button
          disabled={busy || version === "none"}
          onClick={() =>
            act(async () => {
              const v = await api(root + "/normalizers/" + version);
              setSource(v.source);
              setDependencies(v.dependencies.join("\n"));
            })
          }
        >
          Load selected source for editing
        </Button>
        <Label>
          Python source
          <Textarea
            rows={14}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            spellCheck={false}
          />
        </Label>
        <Label>
          Dependencies (one per line)
          <Textarea
            rows={2}
            value={dependencies}
            onChange={(e) => setDependencies(e.target.value)}
            placeholder="Standard-library code needs none"
          />
        </Label>
        <Button
          disabled={busy || !source.trim()}
          onClick={() =>
            act(async () => {
              const v = await api(root + "/normalizers", "POST", {
                source,
                dependencies: dependencies
                  .split("\n")
                  .map((v) => v.trim())
                  .filter(Boolean),
              });
              setVersion(v.id);
              setResult(null);
              await load();
            })
          }
        >
          Save new version
        </Button>
      </details>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  );
}
