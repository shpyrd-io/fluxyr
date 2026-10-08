import { useCallback, useEffect, useState } from "react";
import { api, date, type RecordData } from "./api";
import { Button, Textarea, Badge } from "./components";
import { Label } from "./ui/label/label";
import { SelectField, SelectOption } from "./form-select";

const example = `import urllib.request
import urllib.error
import time

# Replace the URL and interval before activating.
def run(ctx):
    previous = ctx.checkpoint
    ctx.ready()
    while not ctx.stopping:
        try:
            with urllib.request.urlopen("https://example.com", timeout=10) as response:
                current = {"up": response.status < 400}
        except (OSError, urllib.error.URLError):
            current = {"up": False}
        if current != previous:
            ctx.emit(session_key="example.com", event_id=str(time.time_ns()),
                     payload=current, checkpoint=current)
            previous = current
        if ctx.wait(60):
            break
`;

export function WorkerRoutine({
  routine,
  reload,
}: {
  routine: RecordData;
  reload: () => Promise<any>;
}) {
  const root = `/routines/${routine.id}/worker`;
  const [info, setInfo] = useState<RecordData | null>(null);
  const [version, setVersion] = useState("none");
  const [source, setSource] = useState(example);
  const [dependencies, setDependencies] = useState("");
  const [secrets, setSecrets] = useState("");
  const [seconds, setSeconds] = useState("5");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<RecordData | null>(null);
  const load = useCallback(async () => {
    const value = await api(root);
    setInfo(value);
    return value;
  }, [root]);
  useEffect(() => {
    load()
      .then((v) => setVersion(v.version_id || "none"))
      .catch((e) => setError(e.message));
  }, [load]);
  async function act(fn: () => Promise<any>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
      await reload();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const lines = (text: string) =>
    text
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <section className="reactive-inbox">
      <div className="actions">
        <strong>Continuous worker</strong>
        <Badge
          variant={
            info?.status === "failed"
              ? "CRITICAL"
              : info?.status === "listening"
                ? "ACTIVE"
                : "OFFLINE"
          }
        >
          {info?.status || "Loading"}
        </Badge>
        <Button disabled={busy} onClick={() => act(load)}>
          Refresh status
        </Button>
      </div>
      <p className="muted">
        One Python listener, independent of agent execution slots. Collect
        samples first; activate after testing. Status and logs refresh on
        demand.
      </p>
      <Label>
        Listener version
        <SelectField value={version} onValueChange={setVersion}>
          <SelectOption value="none">Select a version</SelectOption>
          {(info?.versions || []).map((v: RecordData) => (
            <SelectOption key={v.id} value={v.id}>
              {v.id.slice(0, 8)} · {v.tested_at ? "Tested" : "Untested"} ·{" "}
              {date(v.created_at)}
            </SelectOption>
          ))}
        </SelectField>
      </Label>
      <div className="actions">
        <Button
          disabled={busy || version === "none"}
          onClick={() =>
            act(() =>
              api(root, "PATCH", { version_id: version, mode: "collecting" }),
            )
          }
        >
          Collect samples
        </Button>
        <Button
          disabled={busy || version === "none"}
          onClick={() =>
            act(() =>
              api(root, "PATCH", { version_id: version, mode: "active" }),
            )
          }
        >
          Activate tested version
        </Button>
        <Button
          disabled={busy}
          onClick={() => act(() => api(root, "PATCH", { mode: "disabled" }))}
        >
          Stop listener
        </Button>
        <Button
          disabled={
            busy || !info?.version_id || routine.reactive?.mode === "disabled"
          }
          onClick={() => act(() => api(root, "PATCH", { restart: true }))}
        >
          Reconnect / reset failures
        </Button>
      </div>
      {!!info?.failures && (
        <p className="form-error">
          {info.failures} consecutive failures.{" "}
          {info.failures >= 5
            ? "Automatic restarts stopped; fix the listener and reconnect."
            : `Retry after ${date(info.retry_at)}.`}
        </p>
      )}
      <details>
        <summary>Code and smoke test</summary>
        <p>
          The test uses real network connections and declared Vault items. It
          saves samples without creating agent jobs or changing the live
          checkpoint. Stop collection before testing.
        </p>
        <Button
          disabled={busy || version === "none"}
          onClick={() =>
            act(async () => {
              const v = await api(`${root}/versions/${version}`);
              setSource(v.source);
              setDependencies(v.dependencies.join("\n"));
              setSecrets(v.secrets.join("\n"));
            })
          }
        >
          Load selected source
        </Button>
        <Label>
          Python · define run(ctx)
          <Textarea
            rows={16}
            value={source}
            onChange={(e) => setSource(e.target.value)}
            spellCheck={false}
          />
        </Label>
        <Label>
          Dependencies · one package requirement per line
          <Textarea
            rows={2}
            value={dependencies}
            onChange={(e) => setDependencies(e.target.value)}
          />
        </Label>
        <Label>
          Vault item names · one per line, never credential values
          <Textarea
            rows={2}
            value={secrets}
            onChange={(e) => setSecrets(e.target.value)}
          />
        </Label>
        <Button
          disabled={busy || !source.trim()}
          onClick={() =>
            act(async () => {
              const v = await api(root + "/versions", "POST", {
                source,
                dependencies: lines(dependencies),
                secrets: lines(secrets),
              });
              setVersion(v.id);
              setResult(null);
            })
          }
        >
          Save new version
        </Button>
        <Label>
          Test duration
          <SelectField value={seconds} onValueChange={setSeconds}>
            {["5", "15", "30"].map((s) => (
              <SelectOption value={s} key={s}>
                {s} seconds
              </SelectOption>
            ))}
          </SelectField>
        </Label>
        <Button
          disabled={busy || version === "none"}
          onClick={() =>
            act(async () =>
              setResult(
                await api(`${root}/versions/${version}/test`, "POST", {
                  seconds: Number(seconds),
                }),
              ),
            )
          }
        >
          {busy ? "Working…" : "Test saved version · no dispatch"}
        </Button>
        {result && <pre>{JSON.stringify(result, null, 2)}</pre>}
      </details>
      <details>
        <summary>Recent listener runs · latest 10</summary>
        {(info?.runs || []).map((r: RecordData) => (
          <details key={r.id}>
            <summary>
              {r.id.slice(0, 8)} · {r.test ? "Test" : "Listener"} · {r.status} ·{" "}
              {date(r.started_at)} · {r.events} events
            </summary>
            {r.error && <p className="form-error">{r.error}</p>}
            <pre>{r.logs || "No diagnostic output"}</pre>
          </details>
        ))}
      </details>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  );
}
