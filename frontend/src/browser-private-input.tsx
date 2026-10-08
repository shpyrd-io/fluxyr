import { useState } from "react";
import { LockKeyhole } from "lucide-react";
import { api, type RecordData } from "./api";
import { Button, Input } from "./components";
import { Label } from "./ui/label/label";

export function BrowserPrivateInput({
  job,
  pending,
  refresh,
}: {
  job: RecordData;
  pending: RecordData;
  refresh: () => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const pua = pending._result?.__pua__ || {};
  const payload = pua.payload || {};
  const passkey = payload.kind === "browser_passkey";
  const [name, setName] = useState(payload.suggested_name || "");
  const id = pending._request_id || pending.call_id;
  async function respond(reject = false) {
    setBusy(true);
    setError("");
    try {
      await api(
        reject
          ? `/jobs/${job.id}/decisions/${id}`
          : `/jobs/${job.id}/private-input/${id}`,
        "POST",
        reject
          ? { decision: "reject" }
          : passkey
            ? { name }
            : payload.vault_item_id
            ? {}
            : { value },
      );
      setValue("");
      setDone(true);
      refresh();
    } catch {
      setValue("");
      setError(
        "Could not deliver private input. The browser may have expired; decline and request it again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="human-request vault-request"
      aria-label="Private browser input"
    >
      <div className="human-heading">
        <h3>
          <LockKeyhole size={18} /> {pua.title}
        </h3>
        <span>{passkey ? "Passkey enrollment" : "Private input"}</span>
      </div>
      <p>
        Destination: <strong>{payload.target?.origin}</strong>
      </p>
      <p className="muted">
        {passkey
          ? "Create a passkey in this browser and save it encrypted in Vault. Future routines can use it to sign in to this site. The agent never receives the private key."
          : "The value goes directly to the browser. The agent receives only the result."}
      </p>
      {payload.target?.expires_at && (
        <p className="muted">
          Available until{" "}
          {new Date(payload.target.expires_at * 1000).toLocaleTimeString()}{" "}
          while the browser stays open.
        </p>
      )}
      {payload.submit && <p>The form will be submitted after filling.</p>}
      {done || pending._decision ? (
        <p role="status">Response saved</p>
      ) : (
        <form
          className="resource-form"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            void respond();
          }}
        >
          {passkey ? (
            <Label>
              Vault name
              <Input required maxLength={200} value={name} disabled={busy || !!payload.vault_item_id}
                onChange={(e) => setName(e.target.value)} />
              <small>{payload.vault_item_id ? "Retry saving the existing passkey; no new registration." : "Confirm to click the registration button on this site."}</small>
            </Label>
          ) : payload.vault_item_id ? (
            <p>
              Allow this browser to use{" "}
              {payload.vault_item_name || "the selected Vault credential"}?
            </p>
          ) : (
            <Label>
              Private value
              <Input
                type="password"
                autoComplete="new-password"
                required
                maxLength={10000}
                value={value}
                disabled={busy}
                onChange={(e) => setValue(e.target.value)}
              />
            </Label>
          )}
          {error && <p role="alert">{error}</p>}
          <div className="dialog-actions">
            <Button disabled={busy} onClick={() => respond(true)}>
              Decline
            </Button>
            <Button type="submit" disabled={busy}>
              {passkey ? (payload.vault_item_id ? "Save existing passkey" : "Register and save passkey") : payload.vault_item_id ? "Allow and continue" : "Send to browser"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
