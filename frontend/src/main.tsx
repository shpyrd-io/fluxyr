import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  useLayoutEffect,
} from "react";
import { createRoot } from "react-dom/client";
import { api, mainSession, RecordData, date } from "./api";
import { Resources } from "./resources";
import { Markdown } from "./markdown";
import { LiveActivity } from "./live-activity";
import { UsageProvider, UsageBadge } from "./usage";
import { ExecutionFlowPanel } from "./execution-flow-panel";
import { buildTimeline } from "./timeline";
import { BuildProgress } from "./build-progress";
import {
  Button,
  Textarea,
  Badge,
  Panel,
  PanelHeader,
  PanelTitle,
  PanelContent,
  Logo,
  Wordmark,
  DebugId,
} from "./components";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
} from "./ui/dialog/dialog";
import {
  Terminal,
  Activity,
  Wrench,
  Boxes,
  Clock3,
  KeyRound,
  Folder,
  Settings2,
  ArrowUp,
  ArrowUpRight,
  BrainCircuit,
  Eraser,
  ChevronRight,
  RefreshCw,
  UserRound,
  Cpu,
  Database,
  Radio,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import { Spinner } from "./ui/spinner/spinner";
import "./styles/scificn.css";
import "./style.css";

type Page =
  | "Workbench"
  | "Executions"
  | "Tools"
  | "Skills"
  | "Routines"
  | "Vault"
  | "Files"
  | "Settings";
const pages: Page[] = [
  "Workbench",
  "Executions",
  "Tools",
  "Skills",
  "Routines",
  "Vault",
  "Files",
  "Settings",
];
const icons = [
  Terminal,
  Activity,
  Wrench,
  Boxes,
  Clock3,
  KeyRound,
  Folder,
  Settings2,
];
function App() {
  const [page, setPage] = useState<Page>("Workbench"),
    [session, setSession] = useState(mainSession),
    [error, setError] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [model, setModel] = useState("");
  const [jobs, setJobs] = useState<RecordData[]>([]),
    [ready, setReady] = useState(false);
  const refresh = useCallback(
    () =>
      api("/jobs")
        .then(setJobs)
        .catch((e) => setError(e.message)),
    [],
  );
  useEffect(() => {
    api("/settings")
      .then((c) => setModel(c.model))
      .catch(() => {});
    api("/health")
      .then((h) => setReady(h.worker === true))
      .catch((e) => setError(e.message));
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);
  const open = (id: string) => {
    setSession(id);
    setPage("Workbench");
  };
  const active = jobs.filter((j) =>
    ["running", "waiting", "queued", "paused", "building"].includes(j.status),
  ).length;
  return (
    <div className={"app" + (collapsed ? " sidebar-collapsed" : "")}>
      <aside className="sidebar">
        <a
          aria-label="Fluxyr Agent workbench"
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            open(mainSession);
          }}
        >
          <Logo className="brand-symbol" />
          <div className="brand-name">
            <Wordmark />
          </div>
        </a>
        <div className="instance">
          <span className={"dot " + (ready ? "online" : "")} />
          <span>Local instance</span> <small>01</small>
        </div>
        <p className="nav-heading">WORKSPACE</p>
        <nav aria-label="Main navigation">
          {pages.map((p, i) => (
            <Button
              key={p}
              variant="GHOST"
              aria-label={p}
              aria-current={p === page ? "page" : undefined}
              title={p}
              className={p === page ? "selected" : ""}
              onClick={() => {
                setPage(p);
                if (p === "Workbench") setSession(mainSession);
              }}
            >
              {React.createElement(icons[i], { size: 17, strokeWidth: 1.6 })}
              <span className="nav-label">{p}</span>
              {p === "Executions" && active > 0 && <b>{active}</b>}
            </Button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Panel className="runtime-panel" notch="sm">
            <PanelHeader>
              <Radio size={13} />
              <PanelTitle>Local runtime</PanelTitle>
            </PanelHeader>
            <PanelContent>
              <div className="runtime-line">
                <span>Worker</span>
                <Badge variant={ready ? "ACTIVE" : "OFFLINE"}>
                  {ready ? "Online" : "Offline"}
                </Badge>
              </div>
              <div className="runtime-line">
                <span>Active jobs</span>
                <strong>{String(active).padStart(2, "0")}</strong>
              </div>
              <div className="runtime-stack">
                <Cpu size={12} /> Python <span>/</span>
                <Database size={12} /> PostgreSQL
              </div>
            </PanelContent>
          </Panel>
          <div className="instance-footer">
            ENGINE VERSION <span>v0.1</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            <Button
              variant="GHOST"
              className="sidebar-toggle"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={() => setCollapsed(!collapsed)}
            >
              {collapsed ? (
                <PanelLeftOpen size={17} />
              ) : (
                <PanelLeftClose size={17} />
              )}
            </Button>
            <span>FLUXYR AGENT</span>
            <ChevronRight size={12} />
            <strong>{page}</strong>
          </div>
          <div className="topbar-status">
            <span className="model-label">
              <Cpu size={13} />
              {model || "Model connection"}
              <UsageBadge total />
            </span>
            <span className="local-label">
              <span className={"dot " + (ready ? "online" : "")} />
              {ready ? "SYSTEM ONLINE" : "CONNECTING"}
            </span>
          </div>
        </header>
        {error && (
          <div className="error global-error" role="alert">
            {error}
            <Button onClick={() => setError("")}>×</Button>
          </div>
        )}
        {page === "Workbench" ? (
          <Chat
            key={session}
            session={session}
            jobs={jobs.filter((j) => j.session_id === session)}
            allJobs={jobs}
            refreshJobs={refresh}
            open={open}
            onError={setError}
          />
        ) : page === "Executions" ? (
          <div className="page">
            <div className="page-title">
              <div>
                <p className="eyebrow">OBSERVABILITY</p>
                <h1>Executions</h1>
                <p>Separate contexts. One place to see what happened.</p>
              </div>
              <Button onClick={refresh}>
                <RefreshCw size={14} />
                Refresh
              </Button>
            </div>
            <div className="table">
              <div className="table-head">
                <span>Execution</span>
                <span>Status</span>
                <span>Created</span>
              </div>
              {jobs
                .filter((j) => j.session_id !== mainSession)
                .map((j) => (
                  <Button
                    className="table-row"
                    key={j.id}
                    onClick={() => open(j.session_id)}
                  >
                    <span>
                      <strong>{j.prompt.slice(0, 100)}</strong>
                      <DebugId id={j.id} />
                    </span>
                    <Status status={j.status} />
                    <span>{date(j.created_at)}</span>
                  </Button>
                ))}
              {jobs.filter((j) => j.session_id !== mainSession).length ===
                0 && (
                <Empty
                  title="No executions yet"
                  text="Run a routine or test a Python action to start an isolated execution."
                />
              )}
            </div>
          </div>
        ) : (
          <Resources page={page} onError={setError} open={open} />
        )}
      </main>
    </div>
  );
}
function Status({ status = "queued" }: { status: string }) {
  const variant = ["running", "building"].includes(status)
    ? "SCANNING"
    : ["succeeded", "completed", "active"].includes(status)
      ? "ACTIVE"
      : ["failed", "interrupted", "cancelled", "error"].includes(status)
        ? "CRITICAL"
        : ["paused", "waiting"].includes(status)
          ? "WARNING"
          : "OFFLINE";
  return (
    <Badge variant={variant} className={"status " + status}>
      {status === "succeeded" ? "completed" : status.replaceAll("_", " ")}
    </Badge>
  );
}
export function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <Terminal size={28} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function Chat({
  session,
  jobs,
  refreshJobs,
  allJobs,
  open,
  onError,
}: {
  session: string;
  jobs: RecordData[];
  refreshJobs: () => void;
  allJobs: RecordData[];
  open: (id: string) => void;
  onError: (x: string) => void;
}) {
  const [messages, setMessages] = useState<RecordData[]>([]),
    [events, setEvents] = useState<RecordData[]>([]),
    [draft, setDraft] = useState(""),
    [sending, setSending] = useState(false),
    [title, setTitle] = useState(""),
    [kind, setKind] = useState("");
  const [generation, setGeneration] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const memoryTrigger = useRef<HTMLButtonElement>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memory, setMemory] = useState<RecordData | null>(null);
  const streamRef = useRef<HTMLDivElement>(null),
    contentRef = useRef<HTMLDivElement>(null),
    followLatest = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const load = useCallback(
    () =>
      api("/sessions/" + session)
        .then((d) => {
          setMessages(d.messages);
          setLoaded(true);
          setTitle(d.session.title);
          setKind(d.session.kind);
        })
        .catch((e) => onError(e.message)),
    [session, onError],
  );
  useEffect(() => {
    load();
    setEvents([]);
    const stream = new EventSource("/api/events?session_id=" + session);
    let buffer: RecordData[] = [];
    let flush: ReturnType<typeof setTimeout> | undefined;
    stream.onmessage = (e) => {
      const event = JSON.parse(e.data);
      buffer.push(event);
      if (!flush)
        flush = setTimeout(() => {
          const batch = buffer;
          buffer = [];
          flush = undefined;
          setEvents((old) => {
            const seen = new Set(old.map((ev) => ev.id));
            return [
              ...old,
              ...batch.filter((ev) => {
                if (seen.has(ev.id)) return false;
                seen.add(ev.id);
                return true;
              }),
            ];
          });
        }, 50);
      if (
        [
          "started",
          "building",
          "succeeded",
          "failed",
          "cancelled",
          "waiting",
          "paused",
          "queued",
        ].includes(event.type)
      ) {
        load();
        refreshJobs();
      }
    };
    return () => {
      stream.close();
      clearTimeout(flush);
    };
  }, [session, load, refreshJobs, generation]);
  const timeline = useMemo(
    () => buildTimeline(messages, events),
    [messages, events],
  );
  const jumpToLatest = useCallback(() => {
    followLatest.current = true;
    setShowLatest(false);
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);
  useLayoutEffect(() => {
    if (followLatest.current) jumpToLatest();
  }, [timeline, jumpToLatest]);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (followLatest.current) jumpToLatest();
    });
    if (contentRef.current) observer.observe(contentRef.current);
    return () => observer.disconnect();
  }, [jumpToLatest]);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    followLatest.current = true;
    setSending(true);
    try {
      await api("/sessions/" + session + "/messages", "POST", {
        content: draft,
      });
      setDraft("");
      load();
      refreshJobs();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setSending(false);
    }
  }
  async function control(id: string, action: string) {
    setControlBusy(true);
    try {
      await api(`/jobs/${id}/${action}`, "POST", {});
      await refreshJobs();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setControlBusy(false);
    }
  }
  async function clearContext() {
    if (
      !window.confirm(
        "Clear this conversation and all its saved memories? History will be archived. Skills, files, vault and routines are preserved.",
      )
    )
      return;
    try {
      await api(`/sessions/${session}/clear`, "POST", {});
      setMessages([]);
      setEvents([]);
      setMemory(null);
      setGeneration((v) => v + 1);
      load();
      refreshJobs();
    } catch (e: any) {
      onError(e.message);
    }
  }
  const unfinished = jobs.filter((j) =>
    ["running", "waiting", "paused", "queued", "building"].includes(j.status),
  );
  const active = [...unfinished].sort(
    (a, b) =>
      Number(a.status === "queued") - Number(b.status === "queued") ||
      a.created_at - b.created_at,
  )[0];
  return (
    <div className="chat-workspace">
      <div className="chat">
        <div className="chat-title">
          <div>
            <p className="eyebrow">
              {session === mainSession
                ? "01 / AGENT CONSOLE"
                : kind === "build"
                  ? "SKILL BUILDER"
                  : "ISOLATED EXECUTION"}
            </p>
            <h1>{session === mainSession ? "Agent workbench" : title}</h1>
            {session === mainSession && (
              <p className="chat-subtitle">
                Build skills. Automate work. Keep the context.
              </p>
            )}
          </div>
          <div className="session-actions">
            <Button
              ref={memoryTrigger}
              onClick={async () => {
                try {
                  setMemory(await api(`/sessions/${session}/memory`));
                  setMemoryOpen(true);
                } catch (e: any) {
                  onError(e.message);
                }
              }}
            >
              <BrainCircuit size={14} /> Memory
            </Button>
            <Button
              onClick={clearContext}
              disabled={unfinished.length > 0}
              title={
                unfinished.length
                  ? "Finish or cancel executions before clearing context"
                  : "Archive history and clear conversation and memories"
              }
            >
              <Eraser size={14} /> Clear context
            </Button>
          </div>
        </div>
        <div className="session-strip">
          <span>
            <span className="dot online" />
            {session === mainSession
              ? "PERSISTENT SESSION"
              : "ISOLATED CONTEXT"}
          </span>
          <code>{session.slice(0, 8)}</code>
          <span className="session-mode">
            {active ? active.status.toUpperCase() : "READY FOR INPUT"}
          </span>
        </div>
        <Dialog open={memoryOpen} onOpenChange={setMemoryOpen}>
          <DialogContent
            className="memory-dialog"
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              memoryTrigger.current?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>Agent memory</DialogTitle>
              <DialogDescription>
                Saved knowledge available to this session.
              </DialogDescription>
            </DialogHeader>
            <DialogBody className="memory-content">
              {memory && (
                <>
                  <div className="memory-section-title">
                    <BrainCircuit size={15} /> Facts{" "}
                    <Badge variant="OFFLINE">
                      {Object.keys(memory.semantic?.items || {}).length}
                    </Badge>
                  </div>
                  {Object.entries(memory.semantic?.items || {}).map(
                    ([key, value]: [string, any]) => (
                      <Panel className="memory-fact" key={key} notch="sm">
                        <small>{key}</small>
                        <p>
                          {typeof value.content === "string"
                            ? value.content
                            : JSON.stringify(value.content)}
                        </p>
                      </Panel>
                    ),
                  )}
                  {!Object.keys(memory.semantic?.items || {}).length && (
                    <p className="muted">
                      No saved facts yet. Ask your agent to remember something.
                    </p>
                  )}
                  <details>
                    <summary>Episodes, patterns & extraction queue</summary>
                    <pre>
                      {JSON.stringify(
                        {
                          episodic: memory.episodic,
                          implicit: memory.implicit,
                          extraction: memory._extraction,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </>
              )}
            </DialogBody>
          </DialogContent>
        </Dialog>
        <div
          className="conversation"
          ref={streamRef}
          onScroll={() => {
            const el = streamRef.current;
            if (!el) return;
            const nearBottom =
              el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            followLatest.current = nearBottom;
            setShowLatest(!nearBottom);
          }}
        >
          <div ref={contentRef}>
            {loaded && messages.length === 0 && (
              <div className="welcome">
                <div className="welcome-symbol">
                  <Logo />
                  <span className="welcome-orbit" />
                </div>
                <p className="eyebrow">ONE AGENT. ENDLESS POSSIBILITIES.</p>
                <h2>What should your agent learn?</h2>
                <p>
                  Build a skill, connect an API, or turn a recurring task into a
                  routine.
                  <br />
                  Everything runs here, on your infrastructure.
                </p>
                <div className="suggestions">
                  {[
                    "Create a skill that calls a weather API",
                    "Show my available tools and skills",
                    "Help me build a scheduled routine",
                  ].map((t) => (
                    <Button key={t} onClick={() => setDraft(t)}>
                      {t}
                      <ArrowUpRight size={16} />
                    </Button>
                  ))}
                </div>
              </div>
            )}
            {timeline.map((item) => (
              <div id={`trace-${item.id}`} key={item.id}>
                {" "}
                {item.kind === "event" ? (
                  <EventCard
                    key={item.id}
                    event={item.event!}
                    open={open}
                    jobs={allJobs}
                  />
                ) : item.kind === "reasoning" ? (
                  <Reasoning
                    key={item.id}
                    text={item.text}
                    live={!!item.live}
                  />
                ) : (
                  <div key={item.id} className={"message " + item.kind}>
                    {item.kind === "user" ? (
                      <div className="avatar">
                        <UserRound size={16} />
                      </div>
                    ) : (
                      <Logo className="avatar" />
                    )}
                    <div className="message-body">
                      <div className="message-label">
                        {item.kind === "user" ? "You" : "Fluxyr Agent"}
                        <time>
                          {new Date(item.createdAt * 1000).toLocaleTimeString(
                            [],
                            {
                              hour: "2-digit",
                              minute: "2-digit",
                            },
                          )}
                        </time>
                      </div>
                      <div
                        className={
                          "message-text" + (item.live ? " streaming" : "")
                        }
                      >
                        <Markdown>{item.text}</Markdown>
                        {item.live && (
                          <Spinner
                            size="MD"
                            className="stream-spinner"
                            aria-label="Generating response"
                          />
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
            {jobs
              .filter((j) => j.status === "waiting")
              .map((j) => (
                <div key={j.id} className="human-panel">
                  <p className="eyebrow">WAITING FOR YOU</p>
                  {j.pending
                    .filter((p: RecordData) => p._status === "parked")
                    .map((p: RecordData) => (
                      <HumanRequest
                        key={p.call_id}
                        job={j}
                        pending={p}
                        refresh={refreshJobs}
                        onError={onError}
                      />
                    ))}
                </div>
              ))}
          </div>
        </div>
        {showLatest && (
          <Button className="jump-latest" onClick={jumpToLatest}>
            ↓ Latest messages
          </Button>
        )}
        <div className="composer-area">
          <LiveActivity events={events} jobs={allJobs} open={open} />
          {active && (
            <div className="activity">
              <Status status={active.status} />
              <DebugId id={active.id} />
              <span>
                {unfinished.length > 1
                  ? `${unfinished.length - 1} queued in this session`
                  : "Execution state is saved locally"}
              </span>
              {active.status === "paused" ? (
                <Button onClick={() => control(active.id, "resume")}>
                  Resume
                </Button>
              ) : ["running", "building"].includes(active.status) ? (
                <Button
                  disabled={controlBusy || !!active.control}
                  onClick={() => control(active.id, "pause")}
                >
                  {active.control === "pause" ? "Pausing…" : "Pause"}
                </Button>
              ) : null}
              <Button
                disabled={controlBusy || active.control === "cancel"}
                onClick={() => control(active.id, "cancel")}
              >
                {active.control === "cancel" ? "Cancelling…" : "Cancel"}
              </Button>
            </div>
          )}
          <form className="composer" onSubmit={send}>
            <Textarea
              aria-label="Message your agent"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                session === mainSession
                  ? "Give your agent something to build or do…"
                  : "Continue this execution’s conversation…"
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(e);
                }
              }}
            />
            <div className="composer-footer">
              <span>
                <Terminal size={13} />{" "}
                {active
                  ? "Your message will be queued"
                  : "Local tools · Persistent memory"}
              </span>
              <Button
                className="send"
                type="submit"
                disabled={sending || !draft.trim()}
                aria-label="Send message"
              >
                <ArrowUp size={19} />
              </Button>
            </div>
          </form>
          <small className="composer-note">
            <span>
              <kbd>Enter</kbd> send <span className="note-divider">/</span>{" "}
              <kbd>Shift + Enter</kbd> new line
            </span>
          </small>
        </div>
      </div>
      <ExecutionFlowPanel
        jobs={allJobs}
        messages={messages}
        events={events}
        onJump={(id) => {
          followLatest.current = false;
          document
            .getElementById(`trace-${id}`)
            ?.scrollIntoView({ block: "center", behavior: "smooth" });
        }}
      />
    </div>
  );
}
function Reasoning({ text, live }: { text: string; live: boolean }) {
  const [expanded, setExpanded] = useState(live);
  return (
    <details
      className="reasoning"
      open={expanded}
      onToggle={(e) => setExpanded(e.currentTarget.open)}
    >
      <summary>
        <BrainCircuit size={14} /> {live ? "Reasoning…" : "Reasoning"}
        <span className="reasoning-hint">{live ? "LIVE" : "VIEW TRACE"}</span>
      </summary>
      <Markdown>{text || "Waiting for the model…"}</Markdown>
    </details>
  );
}
function EventCard({
  event: e,
  open,
  jobs,
}: {
  event: RecordData;
  open: (id: string) => void;
  jobs: RecordData[];
}) {
  const dismissKey = `fluxyr-dismissed-error:${e.job_id}`;
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(dismissKey) === "1",
  );
  if (["failed", "interrupted"].includes(e.type)) {
    if (dismissed) return null;
    return (
      <div className="error execution-error">
        <details>
          <summary>
            Execution {e.type}. <span>Show details</span>
          </summary>
          <pre>
            {e.payload.error ||
              e.payload.outcome?.failures?.join("\n") ||
              "The execution did not complete successfully."}
          </pre>
        </details>
        <Button
          aria-label="Dismiss execution error"
          onClick={() => {
            localStorage.setItem(dismissKey, "1");
            setDismissed(true);
          }}
        >
          ×
        </Button>
      </div>
    );
  }
  if (e.type === "build_started")
    return <BuildProgress event={e} open={open} />;
  if (["execution_report", "execution_started"].includes(e.type))
    return (
      <Button
        className="execution-report"
        onClick={() => open(e.payload.session_id)}
      >
        <span>↗</span>
        <div>
          <strong>{e.payload.prompt?.slice(0, 90)}</strong>
          <small>
            {e.type === "build_started"
              ? "Open the isolated builder and follow its progress"
              : "Open execution and inspect its context"}
          </small>
        </div>
        <span className="tool-meta">
          <UsageBadge jobId={e.payload.job_id || e.job_id} />
          <DebugId id={e.payload.job_id || e.job_id} />
          <Status
            status={
              jobs.find((j) => j.id === (e.payload.job_id || e.job_id))
                ?.status || e.payload.status
            }
          />
        </span>
      </Button>
    );
  if (e.type === "preview")
    return (
      <div className="preview-card">
        <a href={e.payload.url} target="_blank" rel="noreferrer">
          {e.payload.title} ↗
        </a>
        <iframe
          title={e.payload.title}
          src={e.payload.url}
          sandbox="allow-scripts"
        />
      </div>
    );
  if (e.type === "progress")
    return <div className="progress">↳ {e.payload.text}</div>;
  return (
    <details className="tool-event">
      <summary>
        <Wrench size={14} />
        <code>{e.payload.tool_name || "Tool call"}</code>
        <span className="tool-meta">
          <UsageBadge callId={e.payload.model_call_id} />
          <DebugId id={String(e.id)} label="event" />
          <span className="tool-result-label">
            {e.type === "tool_begin"
              ? "running"
              : e.payload.result?.error
                ? "error"
                : e.payload.mode === "wait"
                  ? "waiting"
                  : "completed"}
          </span>
        </span>
      </summary>
      <div className="tool-debug-ids">
        <span>
          Job: <code>{e.job_id}</code>
        </span>
        <span>
          Call: <code>{e.payload.tool_call_id || "unavailable"}</code>
        </span>
        <span>
          Event: <code>{e.id}</code>
        </span>
      </div>
      <pre>
        {JSON.stringify(
          e.type === "tool_begin" ? e.payload.args : e.payload.result,
          null,
          2,
        )}
      </pre>
    </details>
  );
}
function HumanRequest({
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
  const [answer, setAnswer] = useState(""),
    [busy, setBusy] = useState(false);
  const pua = p._result?.__pua__,
    decided = !!p._decision;
  async function decide(decision: string, text?: string) {
    setBusy(true);
    try {
      await api(`/jobs/${job.id}/decisions/${p.call_id}`, "POST", {
        decision,
        result: { answer: text ?? answer },
        reason: decision === "reject" ? answer : undefined,
      });
      refresh();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="human-request">
      <h3>{pua?.title || p.name}</h3>
      <p>{pua?.message || p._result?.question}</p>
      {decided ? (
        <span className="status succeeded">Response saved</span>
      ) : (
        <>
          <Textarea
            aria-label="Your response"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Your response…"
          />
          {pua?.payload?.choices?.map((c: string) => (
            <Button
              key={c}
              onClick={() => decide("complete", c)}
              disabled={busy}
            >
              {c}
            </Button>
          ))}
          <div className="actions">
            <Button
              className="primary"
              disabled={busy}
              onClick={() =>
                decide(pua?.payload?.preflight ? "approve" : "complete")
              }
            >
              {pua?.payload?.preflight ? "Approve execution" : "Send response"}
            </Button>
            <Button disabled={busy} onClick={() => decide("reject")}>
              Decline
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <UsageProvider>
    <App />
  </UsageProvider>,
);
