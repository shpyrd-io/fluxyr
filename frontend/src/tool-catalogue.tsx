import { SelectField, SelectOption } from "./form-select";
import { useEffect, useState } from "react";
import { api, type RecordData } from "./api";
import { Input } from "./components";
import { ChevronRight, Search } from "lucide-react";

const toolGroups: Record<string, string[]> = {
  "Skills & code": [
    "list_skills",
    "get_skill",
    "create_skill",
    "update_skill",
    "build_skill",
    "rebuild_action",
    "submit_plan",
    "create_action",
    "test_action",
    "activate_action",
    "update_action_description",
    "set_skill_enabled",
    "delete_skill",
  ],
  "Web & documentation": ["web_browse", "web_extract", "tech_doc"],
  "Files & shell": ["read", "write", "edit", "bash", "render_preview"],
  Memory: ["read_memory", "save_in_memory", "delete_memory", "observe_pattern"],
  Routines: [
    "list_routines",
    "create_routine",
    "update_routine",
    "run_routine",
    "list_routine_receipts",
    "inspect_routine_receipt",
    "create_routine_normalizer",
    "test_routine_normalizer",
    "inspect_routine_normalizer",
    "configure_reactive_routine",
    "replay_routine_receipt",
  ],
  "Execution & interaction": [
    "list_executions",
    "inspect_execution",
    "finish_execution",
    "ask_human",
  ],
  Vault: ["vault_list", "check_vault_credential", "manage_vault_credential"],
  Agent: ["list_tools"],
};
const toolSummaries: Record<string, string> = {
  list_tools: "List the agent’s available tools and their parameters.",
  web_browse: "Read a web page as Markdown.",
  web_extract: "Read a specific part of a web page using a CSS selector.",
  tech_doc: "Turn API documentation into an integration reference.",
  ask_human: "Pause execution and wait for a human answer.",
  list_skills: "List skills, actions, versions and test results.",
  get_skill: "Inspect one skill’s instructions, code and test results.",
  create_skill: "Define a skill and its technical specification.",
  update_skill: "Edit a skill’s instructions or specification.",
  build_skill: "Delegate Python implementation to the skill builder.",
  rebuild_action: "Rebuild one action from the updated specification.",
  test_action: "Run a candidate action locally and inspect its result.",
  activate_action: "Make a tested action version available to the agent.",
  update_action_description:
    "Improve an action’s description without changing its code.",
  set_skill_enabled: "Enable or disable a skill and its actions.",
  delete_skill: "Remove a skill while keeping its execution history.",
  read_memory: "Read or search saved memories without changing them.",
  save_in_memory: "Save a fact or experience in long-term memory.",
  delete_memory: "Forget a saved fact or episode.",
  read: "Read text by lines or inspect an image.",
  write: "Create a file or replace its full contents.",
  edit: "Replace specific text blocks and inspect the diff.",
  bash: "Run shell commands with streaming output.",
  render_preview: "Display a local file in the conversation.",
  manage_vault_credential:
    "Open a private credential form in chat and wait for you to save it.",
  vault_list:
    "List credentials and safe OAuth configuration; values stay hidden.",
  check_vault_credential:
    "Check credential readiness and OAuth token exchange without exposing secrets.",
  list_routines: "List saved routines and schedules.",
  create_routine: "Create a manual, scheduled or reactive task.",
  update_routine: "Change a routine or its schedule.",
  run_routine: "Start a routine in a separate conversation.",
  list_executions: "List previous and ongoing executions.",
  inspect_execution: "Read an execution’s context, events and result.",
  finish_execution: "Finish the current routine with a report.",
};
function toolGroup(tool: RecordData) {
  const name = tool.name;
  if (tool.source === "application") return "Application tools";
  if (name.startsWith("action_")) return "Skill actions";
  return (
    Object.entries(toolGroups).find(([, names]) => names.includes(name))?.[0] ||
    "Agent"
  );
}
export function ToolCatalogue({ onError }: { onError: (s: string) => void }) {
  const [tools, setTools] = useState<RecordData[]>([]);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("All tools");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api("/tools")
      .then(setTools)
      .catch((e) => onError(e.message))
      .finally(() => setLoaded(true));
  }, [onError]);
  const groups = [
    "Application tools",
    "Skill actions",
    ...Object.keys(toolGroups),
  ].filter((g) => tools.some((t) => toolGroup(t) === g));
  const filtered = tools.filter(
    (t) =>
      (group === "All tools" || toolGroup(t) === group) &&
      `${t.name} ${t.description} ${toolSummaries[t.name] || ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <div className="tool-catalogue">
      <div className="tool-toolbar">
        <div className="tool-search-field">
          <Search size={14} />
          <Input
            aria-label="Search tools"
            placeholder="Find a tool…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <SelectField
          aria-label="Filter tools by category"
          value={group}
          onValueChange={(value) => setGroup(value)}
        >
          <SelectOption>All tools</SelectOption>
          {groups.map((g) => (
            <SelectOption key={g}>{g}</SelectOption>
          ))}
        </SelectField>
        <span className="tool-count">
          {filtered.length} of {tools.length}
        </span>
      </div>
      <div className="tool-column-labels">
        <span>TOOL</span>
        <span>WHAT IT DOES</span>
        <span>INPUTS</span>
        <span />
      </div>
      {groups.map((g) => {
        const members = filtered.filter((t) => toolGroup(t) === g);
        if (!members.length) return null;
        return (
          <section className="tool-group" key={g} aria-label={g}>
            <h2>
              {g}
              <span>{members.length}</span>
              {g === "Skill actions" && <small>Created by your agent</small>}
            </h2>
            {members.map((t) => {
              const properties = Object.entries(
                t.parameters?.properties || {},
              ) as [string, RecordData][];
              return (
                <details className="tool-row" key={t.name}>
                  <summary>
                    <code>{t.name}</code>
                    <span className="tool-summary">
                      {toolSummaries[t.name] || t.description.split(/\.\s/)[0]}
                    </span>
                    <span className="tool-input-count">
                      {properties.length
                        ? `${properties.length} ${properties.length === 1 ? "param" : "params"}`
                        : "None"}
                    </span>
                    <ChevronRight className="tool-chevron" size={13} />
                  </summary>
                  <div className="tool-expanded">
                    <p>{t.description}</p>
                    {properties.length ? (
                      <div className="tool-parameters">
                        <table>
                          <thead>
                            <tr>
                              <th>Parameter</th>
                              <th>Type</th>
                              <th>Details</th>
                            </tr>
                          </thead>
                          <tbody>
                            {properties.map(([name, spec]) => (
                              <tr key={name}>
                                <td>
                                  <code>{name}</code>
                                  <span
                                    className={
                                      t.parameters.required?.includes(name)
                                        ? "param-required"
                                        : "param-optional"
                                    }
                                  >
                                    {t.parameters.required?.includes(name)
                                      ? "required"
                                      : "optional"}
                                  </span>
                                </td>
                                <td>
                                  <code>
                                    {Array.isArray(spec.type)
                                      ? spec.type.join(" | ")
                                      : spec.type ||
                                        (spec.enum ? "enum" : "value")}
                                  </code>
                                </td>
                                <td>
                                  {spec.description || "—"}
                                  {spec.enum && (
                                    <small>
                                      Options:{" "}
                                      {spec.enum
                                        .map((v: unknown) => JSON.stringify(v))
                                        .join(", ")}
                                    </small>
                                  )}
                                  {spec.default !== undefined && (
                                    <small>
                                      Default: {JSON.stringify(spec.default)}
                                    </small>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="muted">This tool takes no parameters.</p>
                    )}
                    <details className="raw-schema">
                      <summary>Full JSON schema</summary>
                      <pre>{JSON.stringify(t.parameters, null, 2)}</pre>
                    </details>
                  </div>
                </details>
              );
            })}
          </section>
        );
      })}
      {!filtered.length && (
        <div className="empty">
          <h3>{loaded ? "No matching tools" : "Loading tools…"}</h3>
          <p>
            {loaded
              ? "Try another search or category."
              : "Reading the agent’s tool catalogue."}
          </p>
        </div>
      )}
    </div>
  );
}
