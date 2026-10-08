import { BrowserPrivateInput } from "./browser-private-input";
import { VaultRequest } from "./vault-request";
import { useState } from "react";
import { api, type RecordData } from "./api";
import { Button, Textarea } from "./components";

function HumanQuestion({
  job,
  pending: p,
  refresh,
  onError,
}: {
  job: RecordData;
  pending: RecordData;
  refresh: () => void;
  onError: (s: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const pua = p._result?.__pua__ || {};
  const payload = pua.payload || {};
  const variant = payload.preflight
    ? "confirm-reject"
    : payload.variant || "continue";
  const choices = (payload.choices || payload.metadata?.choices || []).map(
    (c: any) => (typeof c === "string" ? { label: c } : c),
  );
  async function decide(decision: string, result?: RecordData) {
    setBusy(true);
    try {
      await api(
        `/jobs/${job.id}/decisions/${p._request_id || p.call_id}`,
        "POST",
        {
          decision,
          result,
          reason: decision === "reject" ? answer : undefined,
        },
      );
      refresh();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const title = pua.title || p.name;
  const message = pua.message || p._result?.question;
  return (
    <section className="human-request" aria-label="Human interaction">
      <div className="human-heading">
        <h3>{title}</h3>
        <span>
          {payload.preflight
            ? "Before execution"
            : variant === "choices"
              ? "Choose one option"
              : variant === "confirm-reject"
                ? "Confirmation"
                : "Your input"}
        </span>
      </div>
      {message && message !== title && <p>{message}</p>}
      {p._decision ? (
        <span className="status succeeded">Response saved</span>
      ) : (
        <>
          {rejecting ? (
            <Textarea
              aria-label="Reason for declining"
              placeholder="Reason (optional)…"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
            />
          ) : variant === "continue" ? (
            <Textarea
              aria-label="Your response"
              placeholder={payload.placeholder || "Your response…"}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
            />
          ) : variant === "choices" ? (
            <div className="human-choices">
              {choices.map((choice: RecordData, index: number) => {
                // Only local preview routes are accepted; raw HTML always uses a sandbox.
                const url =
                  typeof choice.url === "string" &&
                  choice.url.startsWith("/preview/")
                    ? choice.url
                    : undefined;
                return (
                  <article className="human-choice" key={index}>
                    {choice.preview_type === "html" &&
                      (url || choice.content) && (
                        <iframe
                          title={`Preview: ${choice.label}`}
                          src={url}
                          srcDoc={url ? undefined : choice.content}
                          sandbox="allow-scripts"
                        />
                      )}
                    {choice.preview_type === "image" && url && (
                      <img src={url} alt={choice.label} />
                    )}
                    {choice.preview_type === "text" && choice.content && (
                      <p className="human-choice-text">{choice.content}</p>
                    )}
                    {url && (
                      <a href={url} target="_blank" rel="noreferrer">
                        Open preview ↗
                      </a>
                    )}
                    {choice.description && <p>{choice.description}</p>}
                    <Button
                      disabled={busy}
                      onClick={() =>
                        decide("complete", {
                          selected_index: index,
                          answer: choice.label,
                        })
                      }
                    >
                      {choice.label}
                    </Button>
                  </article>
                );
              })}
            </div>
          ) : null}
          <div className="actions">
            {rejecting ? (
              <>
                <Button disabled={busy} onClick={() => decide("reject")}>
                  Confirm decline
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => {
                    setRejecting(false);
                    setAnswer("");
                  }}
                >
                  Back
                </Button>
              </>
            ) : (
              <>
                {variant === "continue" && (
                  <Button
                    className="primary"
                    disabled={busy || !answer.trim()}
                    onClick={() => decide("complete", { answer })}
                  >
                    Send response
                  </Button>
                )}
                {variant === "confirm-reject" && (
                  <Button
                    className="primary"
                    disabled={busy}
                    onClick={() => decide("approve")}
                  >
                    {payload.preflight ? "Approve execution" : "Confirm"}
                  </Button>
                )}
                <Button
                  disabled={busy}
                  onClick={() => {
                    setRejecting(true);
                    setAnswer("");
                  }}
                >
                  Decline
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export function HumanRequest(props: Parameters<typeof HumanQuestion>[0]) {
  return ["browser_request_input", "browser_register_passkey"].includes(props.pending.name) ? (
    <BrowserPrivateInput {...props} />
  ) : props.pending.name === "manage_vault_credential" ? (
    <VaultRequest {...props} />
  ) : (
    <HumanQuestion {...props} />
  );
}
