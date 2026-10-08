import { Typography } from "./ui/typography/typography";
import { Status } from "./status";
import { Switch } from "./ui/switch/switch";
import { Separator } from "./ui/separator/separator";
import { SelectField, SelectOption } from "./form-select";
import { Label } from "./ui/label/label";
import React, { useState, useEffect, useCallback } from "react";
import { api, RecordData, date } from "./api";
import { Button, Input, Textarea, Panel, Badge } from "./components";
import { Wrench, KeyRound, Clock3, Folder, FileCode2, Trash2 } from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody,
} from "./ui/dialog/dialog";

import { ResourceCatalogue } from "./resource-catalogue";
import { ToolCatalogue } from "./tool-catalogue";
import { LocalFilePreview } from "./local-file-preview";
import { filePreviewKind, filePreviewUrl } from "./file-preview-kind";

type Props = {
  page: string;
  onError: (s: string) => void;
  open: (id: string) => void;
};
const descriptions: Record<string, string> = {
  Tools:
    "The tools currently available to your agent, with their actual input schemas.",
  Skills: "Instructions and versioned Python actions your agent can use.",
  Routines: "Tasks triggered manually, on a schedule, by webhooks or continuous workers.",
  Vault: "Encrypted secrets, OAuth connections and client certificates.",
  Files: "Your agent’s local data directory. No remote storage.",
  Settings: "Inspect the configuration loaded from your application environment.",
};
export function Resources({ page, onError, open }: Props) {
  return (
    <div className={"page" + (page === "Tools" ? " tools-page" : "")}>
      <div className="page-title">
        <div>
          <p className="eyebrow">INSTANCE RESOURCES</p>
          <Typography variant="H1">{page}</Typography>
          <p>{descriptions[page]}</p>
        </div>
      </div>
      {page === "Settings" ? (
        <Settings onError={onError} />
      ) : page === "Tools" ? (
        <ToolCatalogue onError={onError} />
      ) : page === "Files" ? (
        <Files onError={onError} />
      ) : (
        <ResourceCatalogue
          key={page}
          page={page}
          onError={onError}
          open={open}
        />
      )}
    </div>
  );
}
function JsonEditor({
  label,
  value,
  onChange,
  rows = 8,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  rows?: number;
}) {
  return (
    <Label>
      {label}
      <Textarea
        className="code"
        value={value}
        rows={rows}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </Label>
  );
}
export function Actions({
  skill,
  reload,
  onError,
  open,
}: {
  skill: RecordData;
  reload: () => void;
  onError: (s: string) => void;
  open: (id: string) => void;
}) {
  const [edit, setEdit] = useState(false),
    [source, setSource] = useState(
      'from fluxyr import params, output\n\noutput({"message": params["message"]})\n',
    ),
    [name, setName] = useState("action_"),
    [description, setDescription] = useState(""),
    [parameters, setParameters] = useState(
      '{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}',
    ),
    [dependencies, setDependencies] = useState(""),
    [secrets, setSecrets] = useState(""),
    [approval, setApproval] = useState(false),
    [testInputs, setTestInputs] = useState<Record<string, string>>({});
  function change(tool: RecordData) {
    const v = tool.versions[0];
    setName(tool.name);
    setDescription(tool.description);
    setSource(v.source);
    setParameters(JSON.stringify(v.parameters, null, 2));
    setDependencies(v.dependencies.join("\n"));
    setSecrets(v.secrets.join("\n"));
    setApproval(v.requires_approval);
    setEdit(true);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/actions", "POST", {
        skill_id: skill.id,
        name,
        description,
        source,
        parameters: JSON.parse(parameters),
        dependencies: dependencies.split("\n").filter(Boolean),
        secrets: secrets.split("\n").filter(Boolean),
        requires_approval: approval,
      });
      setEdit(false);
      reload();
    } catch (e: any) {
      onError(e.message);
    }
  }
  async function test(id: string) {
    try {
      const job = await api("/actions/" + id + "/test", "POST", {
        params: JSON.parse(testInputs[id] || "{}"),
      });
      open(job.session_id);
    } catch (e: any) {
      onError(e.message);
    }
  }
  return (
    <div className="actions-list">
      {skill.tools.map((t: RecordData) => (
        <details className="action-detail" key={t.id}>
          <summary>
            ⌘ {t.name}{" "}
            <small>
              {t.active_version ? "Active" : "No active version"} ·{" "}
              {t.versions.length} versions
            </small>
          </summary>
          <p>{t.description}</p>
          <Button onClick={() => change(t)}>Edit as a new version</Button>
          {t.versions.map((v: RecordData) => (
            <div className="version" key={v.id}>
              <div>
                <code>{v.id.slice(0, 8)}</code> <Status status={v.state} />
                {t.active_version === v.id && (
                  <span className="status succeeded">In use</span>
                )}
              </div>
              <details>
                <summary>Python source and parameters</summary>
                <pre>{v.source}</pre>
                <pre>{JSON.stringify(v.parameters, null, 2)}</pre>
              </details>
              {v.test_result && (
                <details>
                  <summary>Last test result</summary>
                  <pre>{JSON.stringify(v.test_result, null, 2)}</pre>
                </details>
              )}
              <JsonEditor
                label="Test inputs (JSON)"
                value={testInputs[v.id] || "{}"}
                onChange={(x) => setTestInputs({ ...testInputs, [v.id]: x })}
                rows={3}
              />
              <div className="actions">
                <Button onClick={() => test(v.id)}>Test locally</Button>
                <Button
                  disabled={!["tested", "active"].includes(v.state)}
                  onClick={async () => {
                    try {
                      await api("/actions/" + v.id + "/activate", "POST", {});
                      reload();
                    } catch (e: any) {
                      onError(e.message);
                    }
                  }}
                >
                  Activate version
                </Button>
              </div>
            </div>
          ))}
        </details>
      ))}
      <Button onClick={() => setEdit(!edit)}>+ Python action</Button>
      {edit && (
        <form className="editor" onSubmit={save}>
          <div className="two-columns">
            <Label>
              Name
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Label>
            <Label>
              Description
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </Label>
          </div>
          <JsonEditor
            label="Python source"
            value={source}
            onChange={setSource}
            rows={12}
          />
          <JsonEditor
            label="Parameters (JSON Schema)"
            value={parameters}
            onChange={setParameters}
          />
          <div className="two-columns">
            <Label>
              Dependencies (one requirement per line)
              <Textarea
                value={dependencies}
                onChange={(e) => setDependencies(e.target.value)}
              />
            </Label>
            <Label>
              Vault names (one per line)
              <Textarea
                value={secrets}
                onChange={(e) => setSecrets(e.target.value)}
              />
            </Label>
          </div>
          <Label className="checkbox">
            <Switch
              type="button"
              checked={approval}
              onCheckedChange={setApproval}
            />
            Require human approval before execution
          </Label>
          <div className="actions">
            <Button type="submit" className="primary">
              Save candidate
            </Button>
            <Button type="button" onClick={() => setEdit(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
function Settings({ onError }: { onError: (s: string) => void }) {
  const [config, setConfig] = useState<RecordData | null>(null);
  useEffect(() => { api("/settings").then(setConfig).catch(e => onError(e.message)); }, [onError]);
  if (!config) return <p>Loading settings…</p>;
  const fields: [string, string][] = [
    ["provider", "FLUXYR_PROVIDER"], ["model", "FLUXYR_MODEL"],
    ["agent_name", "FLUXYR_AGENT_NAME"],
    ["provider_endpoint", "FLUXYR_PROVIDER_ENDPOINT"], ["provider_format", "FLUXYR_PROVIDER_FORMAT"], ["max_tokens", "FLUXYR_MAX_TOKENS"],
    ["reasoning_effort", "FLUXYR_REASONING_EFFORT"], ["thinking_mode", "FLUXYR_THINKING_MODE"],
    ["thinking_budget", "FLUXYR_THINKING_BUDGET"],
  ];
  return <div className="panel settings">
    <h2>Environment configuration</h2>
    <p>Edit your application’s .env or deployment environment, then restart to apply changes.</p>
    {fields.map(([key, variable]) => <Label key={key}>{variable}<Input readOnly value={String(config[key] ?? "")} /></Label>)}
    <Separator />
    <p>{config.provider.toUpperCase()}_API_KEY: {config.configured_keys?.[config.provider] ? "Configured" : "Not configured"}</p>
    <p className="muted">Credential values are never returned by this screen.</p>
  </div>;
}
function Files({ onError }: { onError: (s: string) => void }) {
  const [path, setPath] = useState("."),
    [items, setItems] = useState<RecordData[]>([]),
    [file, setFile] = useState<RecordData | null>(null),
    [preview, setPreview] = useState(""),
    [deleting, setDeleting] = useState<RecordData | null>(null),
    [deleteBusy, setDeleteBusy] = useState(false),
    [deleteError, setDeleteError] = useState("");
  const load = useCallback(
    () =>
      api("/files?path=" + encodeURIComponent(path))
        .then(setItems)
        .catch((e) => onError(e.message)),
    [path, onError],
  );
  useEffect(() => {
    load();
  }, [load]);
  async function edit(p: string) {
    if (filePreviewKind(p) !== "frame") {
      setPreview(filePreviewUrl(p));
      setFile(null);
      return;
    }
    try {
      setFile(await api("/files/content?path=" + encodeURIComponent(p)));
      setPreview("");
    } catch (e: any) {
      onError(e.message);
    }
  }
  return (
    <>
      <div className="resource-toolbar">
        <span className="path">./data/{path === "." ? "" : path}</span>
        <div className="actions">
          <Button
            onClick={() =>
              setPath(path.split("/").slice(0, -1).join("/") || ".")
            }
          >
            ↑ Parent
          </Button>
          <Button
            onClick={() => {
              const name = prompt("New file name");
              if (name)
                setFile({
                  path: (path === "." ? "" : path + "/") + name,
                  content: "",
                });
            }}
          >
            + File
          </Button>
          <Label className="button">
            Upload
            <Input
              type="file"
              hidden
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const form = new FormData();
                form.append("file", f);
                form.append("path", (path === "." ? "" : path + "/") + f.name);
                try {
                  const r = await fetch("/api/files/upload", {
                    method: "POST",
                    body: form,
                  });
                  if (!r.ok) throw new Error((await r.json()).error);
                  load();
                } catch (e: any) {
                  onError(e.message);
                }
              }}
            />
          </Label>
        </div>
      </div>
      <div className="file-layout">
        <div className="panel file-list">
          {items.length === 0 && (
            <p className="muted">This directory is empty.</p>
          )}
          {items.map((item) => (
            <div className="file-row" key={item.path}>
              <Button
                onClick={() =>
                  item.directory ? setPath(item.path) : edit(item.path)
                }
              >
                {item.directory ? (
                  <Folder size={15} />
                ) : (
                  <FileCode2 size={15} />
                )}
                {item.name}
              </Button>
              {!item.directory && (
                <Button
                  onClick={() => {
                    setPreview(filePreviewUrl(item.path));
                    setFile(null);
                  }}
                >
                  Preview ↗
                </Button>
              )}
              <Button
                className="danger"
                aria-label={`Delete ${item.name}`}
                title={`Delete ${item.name}`}
                onClick={() => {
                  setDeleteError("");
                  setDeleting(item);
                }}
              >
                <Trash2 size={14} />
              </Button>
            </div>
          ))}
        </div>
        {file && (
          <form
            className="panel editor"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                setFile(await api("/files/content", "POST", file));
                load();
              } catch (e: any) {
                onError(e.message);
              }
            }}
          >
            <h2>{file.path}</h2>
            <JsonEditor
              label="Contents"
              value={file.content}
              onChange={(content) => setFile({ ...file, content })}
              rows={22}
            />
            <div className="actions">
              <Button type="submit" className="primary">
                Save file
              </Button>
              <Button type="button" onClick={() => setFile(null)}>
                Close
              </Button>
            </div>
          </form>
        )}
        {preview && (
          <LocalFilePreview url={preview} title={decodeURIComponent(preview.slice("/preview/".length))} onClose={() => setPreview("")} />
        )}
      </div>
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !deleteBusy) setDeleting(null);
        }}
      >
        <DialogContent className="resource-dialog delete-dialog">
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              {deleting?.directory
                ? "Only empty folders can be deleted. This cannot be undone."
                : "This permanently deletes the file from your agent’s data directory."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {deleteError && <p role="alert" className="form-error">{deleteError}</p>}
            <div className="dialog-actions">
              <Button disabled={deleteBusy} onClick={() => setDeleting(null)}>
                Cancel
              </Button>
              <Button
                className="danger"
                disabled={deleteBusy}
                onClick={async () => {
                  if (!deleting) return;
                  setDeleteBusy(true);
                  setDeleteError("");
                  try {
                    await api("/files/operation", "POST", {
                      operation: "delete", path: deleting.path,
                    });
                    if (file?.path === deleting.path) setFile(null);
                    const deletedPreview = "/preview/" + deleting.path.split("/").map(encodeURIComponent).join("/");
                    if (preview === deletedPreview) setPreview("");
                    setDeleting(null);
                    await load();
                  } catch (e: any) {
                    setDeleteError(e.message);
                  } finally {
                    setDeleteBusy(false);
                  }
                }}
              >
                {deleteBusy ? "Deleting…" : deleting?.directory ? "Delete folder" : "Delete file"}
              </Button>
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}
