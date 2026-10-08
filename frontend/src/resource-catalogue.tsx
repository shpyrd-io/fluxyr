import { Switch } from "./ui/switch/switch";
import { Separator } from "./ui/separator/separator";
import { SelectField, SelectOption } from "./form-select";
import { Label } from "./ui/label/label";
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
import { VaultForm, vaultKinds as kinds } from "./vault-form";
import { CronEditor } from "./cron-editor";
import { ReactiveRoutine } from "./reactive-routine";
import { describeCron } from "./cron";

type Props = {
  page: string;
  onError: (s: string) => void;
  open: (s: string) => void;
};
const defaults: Record<string, RecordData> = {
  Skills: { name: "", description: "", instruction: "", spec: "" },
  Routines: {
    trigger: "manual",
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
    [deleting, setDeleting] = useState<RecordData | null>(null);
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
    setValues(
      item
        ? page === "Vault"
          ? {
              name: item.name,
              kind: item.type,
              oauth_config: item.oauth_config,
            }
          : { ...defaults[page], ...item }
        : structuredClone(defaults[page]),
    );
    setEditing(true);
  }
  const set = (key: string, value: any) =>
    setValues((v) => ({ ...v, [key]: value }));
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      let body: RecordData;
      if (page === "Skills")
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
          trigger: values.trigger,
          cron: values.cron || null,
          timezone: values.timezone,
          enabled:
            values.trigger === "reactive"
              ? selected
                ? values.enabled
                : true
              : values.enabled,
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
        <Label className="catalogue-search">
          <Search size={15} />
          <Input
            aria-label={`Search ${page.toLowerCase()}`}
            placeholder={`Search ${page.toLowerCase()}…`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </Label>
        <SelectField
          aria-label={`Filter ${page.toLowerCase()}`}
          value={filter}
          onValueChange={(value) => setFilter(value)}
        >
          <SelectOption value="all">All {page.toLowerCase()}</SelectOption>
          {page === "Vault" ? (
            Object.entries(kinds).map(([k, v]) => (
              <SelectOption key={k} value={k}>
                {v}
              </SelectOption>
            ))
          ) : (
            <>
              <SelectOption value="true">Enabled</SelectOption>
              <SelectOption value="false">Disabled</SelectOption>
            </>
          )}
        </SelectField>
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
                      <span>
                        {item.readonly
                          ? `File · ${item.source_path}`
                          : `${item.tools?.length || 0} actions`}
                      </span>
                      <Badge variant={item.enabled ? "ACTIVE" : "OFFLINE"}>
                        {item.enabled ? "Enabled" : "Disabled"}
                      </Badge>
                    </>
                  ) : page === "Routines" ? (
                    <>
                      {item.trigger === "reactive" ? (
                        <Badge
                          variant={
                            item.reactive?.mode === "active"
                              ? "ACTIVE"
                              : "OFFLINE"
                          }
                        >
                          {item.reactive?.mode === "collecting"
                            ? "Collecting examples"
                            : item.reactive?.mode}
                        </Badge>
                      ) : (
                        <>
                          <span title={item.cron || "Manual"}>
                            {describeCron(item.cron)}
                          </span>
                          <small>{item.timezone}</small>
                          <Badge variant={item.enabled ? "ACTIVE" : "OFFLINE"}>
                            {item.enabled ? "Scheduled" : "Disabled"}
                          </Badge>
                        </>
                      )}
                    </>
                  ) : (
                    <Badge variant="OFFLINE">Encrypted</Badge>
                  )}
                </div>
                <div className="catalogue-controls">
                  {page === "Routines" && item.trigger !== "reactive" && (
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
                    disabled={item.readonly}
                    title={
                      item.readonly
                        ? "Edit the Markdown file and restart"
                        : undefined
                    }
                    aria-label={`Edit ${item.name}`}
                    onClick={() => edit(item)}
                  >
                    <Pencil size={14} />
                  </Button>
                  <Button
                    disabled={item.readonly}
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
                      {item.readonly ? (
                        <p className="muted">
                          Managed by {item.source_path}. Edit the Markdown file
                          and restart the application.
                        </p>
                      ) : (
                        <div className="actions">
                          <Button
                            disabled={busy || item.readonly}
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
                            disabled={busy || item.readonly}
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
                      )}
                      <details>
                        <summary>
                          Instructions and technical specification
                        </summary>
                        <h3>Instructions</h3>
                        <pre>{item.instruction}</pre>
                        <h3>Specification</h3>
                        <pre>{item.spec || "No specification saved."}</pre>
                      </details>
                      {!item.readonly && (
                        <Actions
                          skill={item}
                          reload={load}
                          onError={onError}
                          open={open}
                        />
                      )}
                    </>
                  ) : page === "Routines" ? (
                    <>
                      <p>{item.prompt}</p>
                      {item.trigger === "reactive" ? (
                        <ReactiveRoutine
                          key={item.id}
                          routine={item}
                          reload={load}
                          open={open}
                        />
                      ) : (
                        <>
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
                      )}
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
                      {item.type === "oauth2" &&
                        item.oauth_config?.grant_type ===
                          "client_credentials" && (
                          <p>
                            Client credentials · Tokens are obtained
                            automatically when an action uses this credential.
                          </p>
                        )}
                      {item.type === "oauth2" &&
                        item.oauth_config?.grant_type !==
                          "client_credentials" && (
                          <Button
                            disabled={busy || item.readonly}
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
            {page === "Vault" ? (
              <VaultForm
                key={selected?.id || "new"}
                initial={values}
                editing={!!selected}
                onBusyChange={setBusy}
                onCancel={() => {
                  setEditing(false);
                  setValues(structuredClone(defaults[page]));
                }}
                onSave={async ({ name, kind, content }) => {
                  await api(
                    path + (selected ? "/" + selected.id : ""),
                    selected ? "PATCH" : "POST",
                    selected ? { name, content } : { name, kind, content },
                  );
                  setEditing(false);
                  setValues(structuredClone(defaults[page]));
                  await load();
                }}
              />
            ) : (
              <form onSubmit={save} className="resource-form">
                <Label>
                  Name
                  <Input
                    required
                    value={values.name || ""}
                    onChange={(e) => set("name", e.target.value)}
                    autoComplete="off"
                  />
                </Label>
                {page === "Skills" ? (
                  <>
                    <Label>
                      Description
                      <Input
                        value={values.description || ""}
                        onChange={(e) => set("description", e.target.value)}
                      />
                    </Label>
                    <Label>
                      Instructions
                      <Textarea
                        required
                        rows={5}
                        value={values.instruction || ""}
                        onChange={(e) => set("instruction", e.target.value)}
                      />
                    </Label>
                    <Label>
                      Technical specification
                      <Textarea
                        rows={7}
                        value={values.spec || ""}
                        onChange={(e) => set("spec", e.target.value)}
                        placeholder="Actions, inputs, expected outputs, dependencies and examples…"
                      />
                    </Label>
                  </>
                ) : page === "Routines" ? (
                  <>
                    <Label>
                      Task prompt
                      <Textarea
                        required
                        rows={5}
                        value={values.prompt || ""}
                        onChange={(e) => set("prompt", e.target.value)}
                      />
                    </Label>
                    <Label>
                      Trigger
                      <SelectField
                        value={values.trigger || "manual"}
                        disabled={selected?.trigger === "reactive"}
                        onValueChange={(value) => {
                          set("trigger", value);
                          setScheduleValid(true);
                        }}
                      >
                        <SelectOption value="manual">Manual</SelectOption>
                        <SelectOption value="scheduled">Scheduled</SelectOption>
                        <SelectOption value="reactive">
                          Reactive · webhook
                        </SelectOption>
                      </SelectField>
                    </Label>
                    {values.trigger === "reactive" ? (
                      <p className="muted">
                        The routine starts collecting examples without a
                        normalizer. Save to get its webhook URL and inbox.
                      </p>
                    ) : values.trigger === "scheduled" ? (
                      <>
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
                        <Label>
                          When another execution is running
                          <SelectField
                            value={values.overlap}
                            onValueChange={(value) => set("overlap", value)}
                          >
                            <SelectOption value="queue">
                              Queue the next execution
                            </SelectOption>
                            <SelectOption value="skip">
                              Skip this occurrence
                            </SelectOption>
                          </SelectField>
                        </Label>
                        <Label className="check-field">
                          <Switch
                            type="button"
                            disabled={!values.cron}
                            checked={!!values.enabled}
                            onCheckedChange={(checked) =>
                              set("enabled", checked)
                            }
                          />
                          Enable schedule
                        </Label>
                      </>
                    ) : null}
                  </>
                ) : null}
                {error && (
                  <p role="alert" className="form-error">
                    {error}
                  </p>
                )}
                <Separator />
                <div className="dialog-actions">
                  <Button disabled={busy} onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                  <Button
                    type="submit"
                    className="primary"
                    disabled={
                      busy ||
                      (page === "Routines" &&
                        values.trigger === "scheduled" &&
                        (!scheduleValid || !values.cron))
                    }
                  >
                    {busy ? "Saving…" : "Save " + singular}
                  </Button>
                </div>
              </form>
            )}
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
            <Separator />
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
