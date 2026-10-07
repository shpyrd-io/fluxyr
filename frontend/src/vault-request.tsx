import { useState } from "react";
import { KeyRound } from "lucide-react";
import { api, type RecordData } from "./api";
import { VaultForm } from "./vault-form";

export function VaultRequest({
  job,
  pending,
  refresh,
}: {
  job: RecordData;
  pending: RecordData;
  refresh: () => void;
}) {
  const [resolved, setResolved] = useState(false);
  const payload = pending._result?.__pua__?.payload || {};
  const requestId = pending._request_id || pending.call_id;
  return (
    <section
      className="human-request vault-request"
      aria-label="Private Vault form"
    >
      <div className="human-heading">
        <h3>
          <KeyRound size={18} />{" "}
          {payload.action === "edit" ? "Edit credential" : "Create credential"}
        </h3>
        <span>Private Vault input</span>
      </div>
      <p className="muted">
        Values are saved directly in Vault. Only the item name and ID are
        returned to the agent.
      </p>
      {resolved || pending._decision ? (
        <p role="status">Response saved</p>
      ) : (
        <VaultForm
          initial={{
            name: payload.suggested_name || payload.vault_item_name,
            kind: payload.vault_item_type,
            oauth_config: payload.oauth_config,
          }}
          editing={payload.action === "edit"}
          lockKind
          saveLabel="Save and continue"
          onSave={async (body) => {
            await api(`/jobs/${job.id}/vault/${requestId}`, "POST", body);
            setResolved(true);
            refresh();
          }}
          onCancel={async () => {
            await api(`/jobs/${job.id}/decisions/${requestId}`, "POST", {
              decision: "reject",
            });
            setResolved(true);
            refresh();
          }}
        />
      )}
    </section>
  );
}
