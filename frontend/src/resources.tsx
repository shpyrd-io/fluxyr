import React, { useState, useEffect, useCallback } from "react";
import { api, RecordData, date } from "./api";
import { Button, Input, Textarea, Panel, Badge } from "./components";
import { Wrench, KeyRound, Clock3, Folder, FileCode2 } from "lucide-react";

import { ResourceCatalogue } from "./resource-catalogue";
import { ToolCatalogue } from "./tool-catalogue";

type Props = {
  page: string;
  onError: (s: string) => void;
  open: (id: string) => void;
};
const descriptions: Record<string, string> = {
  Tools:
    "The tools currently available to your agent, with their actual input schemas.",
  Skills: "Instructions and versioned Python actions your agent can use.",
  Routines: "Repeatable tasks, run on demand or on your schedule.",
  Vault: "Encrypted secrets, OAuth connections and client certificates.",
  Files: "Your agent’s local data directory. No remote storage.",
  Settings: "Connect your model provider. Keys stay in the local vault.",
};
export function Resources({ page, onError, open }: Props) {
  return (
    <div className={"page" + (page === "Tools" ? " tools-page" : "")}>
      <div className="page-title">
        <div>
          <p className="eyebrow">INSTANCE RESOURCES</p>
          <h1>{page}</h1>
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
    <label>
      {label}
      <Textarea
        className="code"
        value={value}
        rows={rows}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </label>
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
                <code>{v.id.slice(0, 8)}</code>{" "}
                <span className="status">{v.state}</span>
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
            <label>
              Name
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              Description
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
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
            <label>
              Dependencies (one requirement per line)
              <Textarea
                value={dependencies}
                onChange={(e) => setDependencies(e.target.value)}
              />
            </label>
            <label>
              Vault names (one per line)
              <Textarea
                value={secrets}
                onChange={(e) => setSecrets(e.target.value)}
              />
            </label>
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={approval}
              onChange={(e) => setApproval(e.target.checked)}
            />
            Require human approval before execution
          </label>
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
  const [config, setConfig] = useState<RecordData | null>(null),
    [key, setKey] = useState(""),
    [saved, setSaved] = useState(false);
  useEffect(() => {
    api("/settings")
      .then(setConfig)
      .catch((e) => onError(e.message));
  }, [onError]);
  if (!config) return <p>Loading settings…</p>;
  const update = (key: string, value: unknown) => {
    setConfig({ ...config, [key]: value });
    setSaved(false);
  };
  return (
    <form
      className="panel settings"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          await api("/settings", "POST", { ...config, api_key: key });
          setKey("");
          setSaved(true);
          setConfig(await api("/settings"));
        } catch (e: any) {
          onError(e.message);
        }
      }}
    >
      <h2>Model connection</h2>
      <p>Choose the model that powers your agent.</p>
      <label>
        Provider
        <select
          value={config.provider}
          onChange={(e) => update("provider", e.target.value)}
        >
          {["anthropic", "openai", "openrouter"].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </label>
      <label>
        Model identifier
        <Input
          value={config.model}
          onChange={(e) => update("model", e.target.value)}
          required
        />
      </label>
      <label>
        API key{" "}
        <small>
          {config.configured_keys?.[config.provider]
            ? "A key is configured. Leave empty to keep it."
            : "Required before your first chat."}
        </small>
        <Input
          type="password"
          autoComplete="new-password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="Enter a new key…"
        />
      </label>
      {config.provider !== "anthropic" && (
        <label>
          Custom base URL (optional)
          <Input
            value={config.base_url}
            onChange={(e) => update("base_url", e.target.value)}
            placeholder={
              config.provider === "openrouter"
                ? "https://openrouter.ai/api/v1"
                : "https://api.openai.com/v1"
            }
          />
        </label>
      )}
      <div className="two-columns">
        <label>
          Maximum output tokens
          <Input
            type="number"
            min="256"
            max="128000"
            value={config.max_tokens}
            onChange={(e) => update("max_tokens", Number(e.target.value))}
          />
        </label>
        {config.provider === "anthropic" ? (
          <label>
            Thinking mode
            <select
              value={config.thinking_mode}
              onChange={(e) => update("thinking_mode", e.target.value)}
            >
              <option value="none">None</option>
              <option value="adaptive">Adaptive</option>
              <option value="enabled">Enabled with budget</option>
            </select>
          </label>
        ) : (
          <label>
            Reasoning effort
            <select
              value={config.reasoning_effort}
              onChange={(e) => update("reasoning_effort", e.target.value)}
            >
              {["", "low", "medium", "high"].map((x) => (
                <option value={x} key={x}>
                  {x || "Provider default"}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {config.thinking_mode === "enabled" && (
        <label>
          Thinking budget
          <Input
            type="number"
            value={config.thinking_budget}
            onChange={(e) => update("thinking_budget", Number(e.target.value))}
          />
        </label>
      )}
      <div className="actions">
        <Button type="submit" className="primary">
          Save connection
        </Button>
        {saved && <span className="success-text">Saved locally ✓</span>}
      </div>
      <p className="muted">
        Runtime paths: ./data · ./workspace · ./.runtime
        <br />
        No account or sign-in required.
      </p>
    </form>
  );
}
function Files({ onError }: { onError: (s: string) => void }) {
  const [path, setPath] = useState("."),
    [items, setItems] = useState<RecordData[]>([]),
    [file, setFile] = useState<RecordData | null>(null),
    [preview, setPreview] = useState("");
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
          <label className="button">
            Upload
            <input
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
          </label>
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
                    setPreview(
                      "/preview/" +
                        item.path.split("/").map(encodeURIComponent).join("/"),
                    );
                    setFile(null);
                  }}
                >
                  Preview ↗
                </Button>
              )}
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
          <div className="panel preview-card">
            <a target="_blank" href={preview} rel="noreferrer">
              Open file ↗
            </a>
            <iframe
              title="Local file preview"
              src={preview}
              sandbox="allow-scripts"
            />
          </div>
        )}
      </div>
    </>
  );
}
