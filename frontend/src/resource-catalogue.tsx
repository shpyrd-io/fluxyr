import { useState, useEffect, useCallback, type FormEvent } from "react";
import { api, date, type RecordData } from "./api";
import { Button, Input, Textarea, Badge } from "./components";
import {
  Boxes,
  Clock3,
  KeyRound,
  FileKey2,
  Search,
  ChevronRight,
  Pencil,
  Trash2,
  Plus,
  Play,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
} from "./ui/dialog/dialog";
import { Actions } from "./resources";
import { CronEditor } from "./cron-editor";
import { describeCron } from "./cron";

type Props = {
  page: string;
  onError: (s: string) => void;
  open: (s: string) => void;
};
const kinds: Record<string, string> = {
  text: "Secret text",
  key_password: "Key and password",
  access_token: "Access token",
  oauth2: "OAuth 2.0",
  certificate_pem: "PEM certificate",
  certificate_pfx: "PFX / PKCS#12 certificate",
};
type Field = {
  key: string;
  label: string;
  type?: string;
  required?: boolean;
  accept?: string;
};
const vaultFields: Record<string, Field[]> = {
  text: [
    { key: "value", label: "Secret value", type: "password", required: true },
  ],
  key_password: [
    { key: "key", label: "Key / username", required: true },
    { key: "password", label: "Password", type: "password", required: true },
  ],
  access_token: [
    {
      key: "access_token",
      label: "Access token",
      type: "password",
      required: true,
    },
  ],
  oauth2: [
    { key: "client_id", label: "Client ID", required: true },
    { key: "client_secret", label: "Client secret", type: "password" },
    {
      key: "authorization_url",
      label: "Authorization URL",
      type: "url",
      required: true,
    },
    { key: "token_url", label: "Token URL", type: "url", required: true },
    { key: "scope", label: "Scopes (space separated)" },
    { key: "certificate_name", label: "Client certificate (Vault item name)" },
  ],
  certificate_pem: [
    {
      key: "certificate",
      label: "Certificate PEM",
      type: "pem",
      required: true,
      accept: ".pem,.crt,.cer",
    },
    {
      key: "private_key",
      label: "Private key PEM",
      type: "pem",
      required: true,
      accept: ".pem,.key",
    },
    { key: "passphrase", label: "Private key passphrase", type: "password" },
  ],
  certificate_pfx: [
    {
      key: "pfx_base64",
      label: "PFX / PKCS#12 file",
      type: "binary",
      required: true,
      accept: ".pfx,.p12",
    },
    { key: "passphrase", label: "Certificate password", type: "password" },
  ],
};
const defaults: Record<string, RecordData> = {
  Skills: { name: "", description: "", instruction: "", spec: "" },
  Routines: {
    name: "",
    prompt: "",
    cron: "",
    timezone: "UTC",
    enabled: false,
    overlap: "queue",
  },
  Vault: { name: "", kind: "text", content: {} },
};

export function ResourceCatalogue({ page, onError, open }: Props) {
  const [items, setItems] = useState<RecordData[]>([]),
    [loaded, setLoaded] = useState(false),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all");
  const [expanded, setExpanded] = useState<string | null>(null),
    [editing, setEditing] = useState(false),
    [selected, setSelected] = useState<RecordData | null>(null),
    [values, setValues] = useState<RecordData>(defaults[page]);
  const [scheduleValid, setScheduleValid] = useState(true);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [deleting, setDeleting] = useState<RecordData | null>(null),
    [fileNames, setFileNames] = useState<Record<string, string>>({});
  const path = "/" + page.toLowerCase(),
    singular =
      page === "Vault" ? "vault item" : page.slice(0, -1).toLowerCase();
  const load = useCallback(
    () =>
      api(path)
        .then(setItems)
        .catch((e) => onError(e.message))
        .finally(() => setLoaded(true)),
    [path, onError],
  );
  useEffect(() => {
    load();
  }, [load]);
  function edit(item?: RecordData) {
    setScheduleValid(true);
    setSelected(item || null);
    setError("");
    setFileNames({});
    setValues(
      item
        ? page === "Vault"
          ? { name: item.name, kind: item.type, content: {} }
          : { ...defaults[page], ...item }
        : structuredClone(defaults[page]),
    );
    setEditing(true);
  }
  const set = (key: string, value: any) =>
    setValues((v) => ({ ...v, [key]: value }));
  const content = (key: string, value: string) =>
    setValues((v) => ({ ...v, content: { ...v.content, [key]: value } }));
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      let body: RecordData;
      if (page === "Vault") {
        const entries = Object.fromEntries(
          Object.entries(values.content).filter(
            ([, v]) => !selected || v !== "",
          ),
        );
        body = selected
          ? { name: values.name, content: entries }
          : { name: values.name, kind: values.kind, content: entries };
      } else if (page === "Skills")
        body = {
          name: values.name,
          description: values.description,
          instruction: values.instruction,
          spec: values.spec,
        };
      else
        body = {
          name: values.name,
          prompt: values.prompt,
          cron: values.cron || null,
          timezone: values.timezone,
          enabled: values.enabled,
          overlap: values.overlap,
        };
      await api(
        path + (selected ? "/" + selected.id : ""),
        selected ? "PATCH" : "POST",
        body,
      );
      setEditing(false);
      setValues(structuredClone(defaults[page]));
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function upload(field: Field, file?: File) {
    if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024)
        throw new Error("Certificate file must be smaller than 2 MB.");
      if (field.type === "binary") {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 8192)
          binary += String.fromCharCode(
            ...bytes.subarray(offset, offset + 8192),
          );
        content(field.key, btoa(binary));
      } else content(field.key, await file.text());
      setFileNames((names) => ({ ...names, [field.key]: file.name }));
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  }
  async function action(fn: () => Promise<any>) {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const filtered = items.filter(
    (i) =>
      `${i.name} ${i.description || i.prompt || i.type || ""}`
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (filter === "all" ||
        (page === "Vault"
          ? i.type === filter
          : String(!!i.enabled) === filter)),
  );
  const Icon =
    page === "Skills" ? Boxes : page === "Routines" ? Clock3 : KeyRound;
  return (
    <div className="resource-catalogue">
      <div className="catalogue-toolbar">
        <label className="catalogue-search">
          <Search size={15} />
          <Input
            aria-label={`Search ${page.toLowerCase()}`}
            placeholder={`Search ${page.toLowerCase()}…`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label={`Filter ${page.toLowerCase()}`}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All {page.toLowerCase()}</option>
          {page === "Vault" ? (
            Object.entries(kinds).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))
          ) : (
            <>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </>
          )}
        </select>
        <span className="catalogue-count">
          {filtered.length}/{items.length}
        </span>
        <Button className="primary" onClick={() => edit()}>
          <Plus size={14} />
          Add {singular}
        </Button>
      </div>
      <div className="catalogue-list">
        {filtered.map((item) => {
          const isOpen = expanded === item.id;
          const RowIcon =
            page === "Vault" && item.type.startsWith("certificate")
              ? FileKey2
              : Icon;
          return (
            <article className="catalogue-item" key={item.id}>
              <div className="catalogue-row">
                <Button
                  className="catalogue-expand"
                  aria-expanded={isOpen}
                  aria-label={`Details for ${item.name}`}
                  onClick={() => setExpanded(isOpen ? null : item.id)}
                >
                  <ChevronRight size={14} className={isOpen ? "rotated" : ""} />
                  <RowIcon size={18} />
                  <span>
                    <strong>{item.name}</strong>
                    <small>
                      {page === "Vault"
                        ? kinds[item.type]
                        : page === "Skills"
                          ? item.description
                          : item.prompt}
                    </small>
                  </span>
                </Button>
                <div className="catalogue-meta">
                  {page === "Skills" ? (
                    <>
                      <span>{item.tools?.length || 0} actions</span>
                      <Badge variant={item.enabled ? "ACTIVE" : "OFFLINE"}>
                        {item.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </>
                  ) : page === "Routines" ? (
                    <>
                      <span title={item.cron || "Manual"}>
                        {describeCron(item.cron)}
                      </span>
                      <small>{item.timezone}</small>
                      <Badge variant={item.enabled ? "ACTIVE" : "OFFLINE"}>
                        {item.enabled ? "Scheduled" : "Disabled"}
                      </Badge>
                    </>
                  ) : (
                    <Badge variant="OFFLINE">Encrypted</Badge>
                  )}
                </div>
                <div className="catalogue-controls">
                  {page === "Routines" && (
                    <Button
                      aria-label={`Run ${item.name}`}
                      disabled={busy}
                      onClick={() =>
                        action(async () => {
                          const job = await api(
                            path + "/" + item.id + "/run",
                            "POST",
                            {},
                          );
                          open(job.session_id);
                        })
                      }
                    >
                      <Play size={14} />
                    </Button>
                  )}
                  <Button
                    aria-label={`Edit ${item.name}`}
                    onClick={() => edit(item)}
                  >
                    <Pencil size={14} />
                  </Button>
                  <Button
                    aria-label={`Delete ${item.name}`}
                    className="danger"
                    onClick={() => {
                      setError("");
                      setDeleting(item);
                    }}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
              {isOpen && (
                <div className="catalogue-detail">
                  {page === "Skills" ? (
                    <>
                      <div className="actions">
                        <Button
                          disabled={busy}
                          onClick={() =>
                            action(async () => {
                              const job = await api(
                                path + "/" + item.id + "/build",
                                "POST",
                                {},
                              );
                              open(job.session_id);
                            })
                          }
                        >
                          Build actions ↗
                        </Button>
                        <Button
                          disabled={busy}
                          onClick={() =>
                            action(() =>
                              api(path + "/" + item.id, "PATCH", {
                                enabled: !item.enabled,
                              }),
                            )
                          }
                        >
                          {item.enabled ? "Disable" : "Enable"} skill
                        </Button>
                      </div>
                      <details>
                        <summary>
                          Instructions and technical specification
                        </summary>
                        <h3>Instructions</h3>
                        <pre>{item.instruction}</pre>
                        <h3>Specification</h3>
                        <pre>{item.spec || "No specification saved."}</pre>
                      </details>
                      <Actions
                        skill={item}
                        reload={load}
                        onError={onError}
                        open={open}
                      />
                    </>
                  ) : page === "Routines" ? (
                    <>
                      <p>{item.prompt}</p>
                      <dl className="resource-facts">
                        <div>
                          <dt>Schedule</dt>
                          <dd>
                            {describeCron(item.cron)} · {item.timezone}
                            {item.cron && (
                              <code className="routine-cron-code">
                                {item.cron}
                              </code>
                            )}
                          </dd>
                        </div>
                        <div>
                          <dt>Next run</dt>
                          <dd>
                            {item.next_run
                              ? date(item.next_run)
                              : "Not scheduled"}
                          </dd>
                        </div>
                        <div>
                          <dt>Overlapping runs</dt>
                          <dd>
                            {item.overlap === "queue"
                              ? "Queue the next execution"
                              : "Skip while running"}
                          </dd>
                        </div>
                      </dl>
                      <Button
                        disabled={busy || (!item.cron && !item.enabled)}
                        onClick={() =>
                          action(() =>
                            api(path + "/" + item.id, "PATCH", {
                              enabled: !item.enabled,
                            }),
                          )
                        }
                      >
                        {item.enabled ? "Disable" : "Enable"} schedule
                      </Button>
                    </>
                  ) : (
                    <>
                      <p>
                        {kinds[item.type]} · Saved {date(item.created_at)}
                      </p>
                      <p>
                        Values are encrypted and are not returned to the
                        browser.
                      </p>
                      {item.type === "oauth2" && (
                        <Button
                          disabled={busy}
                          onClick={() =>
                            action(async () => {
                              const r = await api(
                                "/vault/oauth/start",
                                "POST",
                                { name: item.name },
                              );
                              window.location.href = r.url;
                            })
                          }
                        >
                          Connect OAuth ↗
                        </Button>
                      )}
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
        {!filtered.length && (
          <div className="catalogue-empty">
            <Icon size={24} />
            <h3>
              {!loaded
                ? "Loading…"
                : items.length
                  ? "No matches"
                  : `No ${page.toLowerCase()} yet`}
            </h3>
            <p>
              {items.length
                ? "Try another search or filter."
                : `Add your first ${singular} to get started.`}
            </p>
          </div>
        )}
      </div>
      <Dialog
        open={editing}
        onOpenChange={(v) => {
          if (!busy) {
            setEditing(v);
            if (!v) setValues(structuredClone(defaults[page]));
          }
        }}
      >
        <DialogContent className="resource-dialog">
          <DialogHeader>
            <DialogTitle>
              {selected ? "Edit" : "Add"} {singular}
            </DialogTitle>
            <DialogDescription>
              {page === "Vault" && selected
                ? "Leave fields blank to keep saved values. Upload a file to replace a certificate."
                : page === "Skills"
                  ? "Define what the skill does. Build its Python actions after saving."
                  : page === "Routines"
                    ? "Describe the task and choose when it runs."
                    : "Choose the credential type and provide its fields."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <form onSubmit={save} className="resource-form">
              <label>
                Name
                <Input
                  required
                  value={values.name || ""}
                  onChange={(e) => set("name", e.target.value)}
                  autoComplete="off"
                />
              </label>
              {page === "Skills" ? (
                <>
                  <label>
                    Description
                    <Input
                      value={values.description || ""}
                      onChange={(e) => set("description", e.target.value)}
                    />
                  </label>
                  <label>
                    Instructions
                    <Textarea
                      required
                      rows={5}
                      value={values.instruction || ""}
                      onChange={(e) => set("instruction", e.target.value)}
                    />
                  </label>
                  <label>
                    Technical specification
                    <Textarea
                      rows={7}
                      value={values.spec || ""}
                      onChange={(e) => set("spec", e.target.value)}
                      placeholder="Actions, inputs, expected outputs, dependencies and examples…"
                    />
                  </label>
                </>
              ) : page === "Routines" ? (
                <>
                  <label>
                    Task prompt
                    <Textarea
                      required
                      rows={5}
                      value={values.prompt || ""}
                      onChange={(e) => set("prompt", e.target.value)}
                    />
                  </label>
                  <CronEditor
                    cron={values.cron || ""}
                    timezone={values.timezone ?? "UTC"}
                    onChange={(cron) =>
                      setValues((v) => ({
                        ...v,
                        cron,
                        enabled: cron ? v.enabled : false,
                      }))
                    }
                    onTimezone={(timezone) => set("timezone", timezone)}
                    onValidity={setScheduleValid}
                  />
                  <label>
                    When another execution is running
                    <select
                      value={values.overlap}
                      onChange={(e) => set("overlap", e.target.value)}
                    >
                      <option value="queue">Queue the next execution</option>
                      <option value="skip">Skip this occurrence</option>
                    </select>
                  </label>
                  <label className="check-field">
                    <input
                      type="checkbox"
                      disabled={!values.cron}
                      checked={!!values.enabled}
                      onChange={(e) => set("enabled", e.target.checked)}
                    />
                    Enable schedule
                  </label>
                </>
              ) : (
                <>
                  <label>
                    Credential type
                    <select
                      disabled={!!selected}
                      value={values.kind}
                      onChange={(e) => {
                        set("kind", e.target.value);
                        set("content", {});
                        setFileNames({});
                      }}
                    >
                      {Object.entries(kinds).map(([k, v]) => (
                        <option key={k} value={k}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </label>
                  {vaultFields[values.kind]?.map((field) => (
                    <label key={field.key}>
                      {field.label}
                      {["pem", "binary"].includes(field.type || "") ? (
                        <>
                          <input
                            type="file"
                            accept={field.accept}
                            aria-label={`Upload ${field.label}`}
                            required={
                              !selected &&
                              field.required &&
                              !values.content?.[field.key]
                            }
                            onChange={(e) => upload(field, e.target.files?.[0])}
                          />
                          {fileNames[field.key] && (
                            <small>
                              {fileNames[field.key]} · ready to save
                            </small>
                          )}
                          {field.type === "pem" && (
                            <Textarea
                              rows={4}
                              aria-label={field.label}
                              value={values.content?.[field.key] || ""}
                              onChange={(e) =>
                                content(field.key, e.target.value)
                              }
                              placeholder={
                                selected
                                  ? "Leave blank to keep saved file"
                                  : "Or paste PEM content here"
                              }
                            />
                          )}
                        </>
                      ) : (
                        <Input
                          type={field.type || "text"}
                          autoComplete="off"
                          required={!selected && field.required}
                          value={values.content?.[field.key] || ""}
                          onChange={(e) => content(field.key, e.target.value)}
                          placeholder={
                            selected ? "Unchanged unless entered" : ""
                          }
                        />
                      )}
                    </label>
                  ))}
                  {values.kind === "oauth2" && (
                    <label>
                      Token authentication
                      <select
                        value={values.content?.token_auth_method || ""}
                        onChange={(e) =>
                          content("token_auth_method", e.target.value)
                        }
                      >
                        <option value="">
                          {selected
                            ? "Keep current method"
                            : "Client secret in request body (default)"}
                        </option>
                        <option value="client_secret_post">
                          Client secret in request body
                        </option>
                        <option value="client_secret_basic">
                          HTTP Basic authentication
                        </option>
                      </select>
                    </label>
                  )}
                </>
              )}
              {error && (
                <p role="alert" className="form-error">
                  {error}
                </p>
              )}
              <div className="dialog-actions">
                <Button disabled={busy} onClick={() => setEditing(false)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  className="primary"
                  disabled={busy || (page === "Routines" && !scheduleValid)}
                >
                  {busy ? "Saving…" : "Save " + singular}
                </Button>
              </div>
            </form>
          </DialogBody>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(v) => {
          if (!v && !busy) setDeleting(null);
        }}
      >
        <DialogContent className="resource-dialog delete-dialog">
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              {page === "Vault"
                ? "This removes the encrypted credential. Actions using it will need another credential."
                : "Execution history is preserved. " +
                  (page === "Skills"
                    ? "The skill and its actions will no longer be available."
                    : "Future scheduled runs will stop; existing executions are preserved.")}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {error && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
            <div className="dialog-actions">
              <Button disabled={busy} onClick={() => setDeleting(null)}>
                Keep {singular}
              </Button>
              <Button
                className="danger"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await api(path + "/" + deleting!.id, "DELETE");
                    setDeleting(null);
                    await load();
                  } catch (e: any) {
                    setError(e.message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Delete {singular}
              </Button>
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </div>
  );
}
