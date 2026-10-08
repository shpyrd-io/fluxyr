import { Status, statusLabel } from "./status";
import { ApprovalKeyDialog } from "./approval-key-dialog";
import { StatusGrid } from "./ui/status-grid/status-grid";
import { Typography } from "./ui/typography/typography";
import { Separator } from "./ui/separator/separator";
import {
  Breadcrumb,
  BreadcrumbList,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "./ui/breadcrumb/breadcrumb";
import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  useLayoutEffect,
} from "react";
import { createRoot } from "react-dom/client";
import { useHistoryWindow } from "./use-history-window";
import { api, mainSession, RecordData, date } from "./api";
import { Resources } from "./resources";
import { Markdown } from "./markdown";
import { LiveActivity } from "./live-activity";
import { ActivityFeed } from "./live-activity-data";
import { UsageProvider, UsageBadge } from "./usage";
import { createPoller } from "./polling";
import { ExecutionFlowPanel } from "./execution-flow-panel";
import { buildTimeline } from "./timeline";
import { executionDiagnostics } from "./execution-diagnostics";
import { pendingInteractions } from "./pending-interactions";
import { HumanRequest } from "./human-request";
import { BuildProgress } from "./build-progress";
import { LocalFilePreview } from "./local-file-preview";
import {
  Button,
  Textarea,
  Badge,
  Panel,
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
  RefreshCw,
  UserRound,
  Cpu,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-mono/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-700.css";
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
  const [engineVersion, setEngineVersion] = useState("");
  const [agentName, setAgentName] = useState("Default Agent");
  const [workerState, setWorkerState] = useState("starting");
  const [jobs, setJobs] = useState<RecordData[]>([]),
    [ready, setReady] = useState(false);
  const jobPoller = useMemo(
    () =>
      createPoller(async () => {
        try {
          const [items, health] = await Promise.all([
            api("/jobs?summary=1"),
            api("/health").catch(() => ({
              worker: false,
              worker_state: "failed",
            })),
          ]);
          setJobs(items);
          setReady(health.worker === true);
          setWorkerState(health.worker_state || "stopped");
        } catch (e: any) {
          setError(e.message);
        }
      }),
    [],
  );
  const refresh = jobPoller.refresh;
  useEffect(() => {
    api("/settings")
      .then((c) => {
        setModel(c.model);
        setAgentName(c.agent_name || "Default Agent");
      })
      .catch(() => {});
    api("/health")
      .then((h) => {
        setReady(h.worker === true);
        setEngineVersion(h.version || "");
      })
      .catch((e) => setError(e.message));
    jobPoller.start();
    const changed = () => {
      void refresh();
    };
    document.addEventListener("visibilitychange", changed);
    window.addEventListener("fluxyr:jobs", changed);
    return () => {
      jobPoller.stop();
      document.removeEventListener("visibilitychange", changed);
      window.removeEventListener("fluxyr:jobs", changed);
    };
  }, [refresh, jobPoller]);
  const open = (id: string) => {
    setSession(id);
    setPage("Workbench");
  };
  const active = jobs.filter((j) =>
    ["running", "waiting", "queued", "paused", "building"].includes(j.status),
  ).length;
  return (
    <UsageProvider session={session}>
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
            <span className="instance-name" title={agentName}>
              {agentName}
            </span>
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
            <StatusGrid
              className="runtime-panel"
              title="Local runtime"
              columns={1}
              systems={[
                {
                  name: "Worker",
                  status: ready ? "ACTIVE" : workerState === "recovering" ? "SCANNING" : "OFFLINE",
                  detail: ready ? undefined : workerState,
                },
                {
                  name: "Jobs",
                  detail: String(active).padStart(2, "0"),
                  status: active ? "SCANNING" : "ACTIVE",
                },
              ]}
            />
            <Separator />
            <div className="instance-footer">
              ENGINE VERSION{" "}
              <span>{engineVersion ? `v${engineVersion}` : "—"}</span>
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
              <Breadcrumb>
                <BreadcrumbList>
                  <BreadcrumbItem>
                    <BreadcrumbLink
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        open(mainSession);
                      }}
                    >
                      Fluxyr Agent
                    </BreadcrumbLink>
                  </BreadcrumbItem>
                  <BreadcrumbSeparator />
                  <BreadcrumbItem>
                    <BreadcrumbPage>
                      {page === "Workbench" && session !== mainSession
                        ? "Execution"
                        : page}
                    </BreadcrumbPage>
                  </BreadcrumbItem>
                </BreadcrumbList>
              </Breadcrumb>
            </div>
            <div className="topbar-status">
              <span className="model-label">
                <Cpu size={13} />
                {model || "Model connection"}
                <UsageBadge total />
              </span>
              <span className="local-label">
                <span className={"dot " + (ready ? "online" : "")} />
                {ready
                  ? "SYSTEM ONLINE"
                  : workerState === "recovering"
                    ? "RECONNECTING WORKER"
                    : workerState.toUpperCase()}
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
                  <Typography variant="H1">Executions</Typography>
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
    </UsageProvider>
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
  const activityFeed = useMemo(() => new ActivityFeed(), [session, generation]);
  const [loaded, setLoaded] = useState(false);
  const memoryTrigger = useRef<HTMLButtonElement>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const diagnosticText = useMemo(
    () => (diagnosticsOpen ? executionDiagnostics(events) : ""),
    [diagnosticsOpen, events],
  );
  const diagnosticsTrigger = useRef<HTMLButtonElement>(null);
  const [memory, setMemory] = useState<RecordData | null>(null);
  const streamRef = useRef<HTMLDivElement>(null),
    contentRef = useRef<HTMLDivElement>(null),
    followLatest = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const replayCursor = useRef<number | null>(null);
  const load = useCallback(
    () =>
      api("/sessions/" + session + "?view=chat")
        .then((d) => {
          replayCursor.current ??= d.event_cursor;
          setMessages(d.messages);
          setLoaded(true);
          setTitle(d.session.title);
          setKind(d.session.kind);
        })
        .catch((e) => onError(e.message)),
    [session, onError],
  );
  useEffect(() => {
    replayCursor.current = null;
    const sessionPoller = createPoller(load, 60_000);
    sessionPoller.start();
    setEvents([]);
    const stream = new EventSource(
      "/api/events?activity=live&session_id=" + session,
    );
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
          activityFeed.update(batch);
          const historyBatch = batch.filter((ev) => ev.type !== "activity");
          if (historyBatch.length)
            setEvents((old) => {
              const seen = new Set(old.map((ev) => ev.id));
              return [
                ...old,
                ...historyBatch.filter((ev) => {
                  if (seen.has(ev.id)) return false;
                  seen.add(ev.id);
                  return true;
                }),
              ];
            });
          if (
            batch.some(
              (event) =>
                event.type === "usage" &&
                replayCursor.current !== null &&
                event.id > replayCursor.current,
            )
          )
            window.dispatchEvent(new Event("fluxyr:usage"));
          if (
            batch.some(
              (event) =>
                replayCursor.current !== null &&
                event.id > replayCursor.current &&
                [
                  "started",
                  "building",
                  "succeeded",
                  "failed",
                  "cancelled",
                  "interrupted",
                  "waiting",
                  "paused",
                  "queued",
                  "execution_report",
                ].includes(event.type),
            )
          ) {
            void sessionPoller.refresh();
            window.dispatchEvent(new Event("fluxyr:usage"));
            refreshJobs();
          }
        }, 50);
    };
    return () => {
      stream.close();
      sessionPoller.stop();
      clearTimeout(flush);
    };
  }, [session, load, refreshJobs, generation, activityFeed]);
  const timeline = useMemo(
    () => buildTimeline(messages, events),
    [messages, events],
  );
  const history = useHistoryWindow(
    timeline,
    streamRef,
    () => followLatest.current,
  );
  const jumpToLatest = useCallback(() => {
    followLatest.current = true;
    setShowLatest(false);
    history.goToLatest();
  }, [history.goToLatest]);
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
            <Breadcrumb
              className="chat-breadcrumb"
              aria-label="Session breadcrumb"
            >
              <BreadcrumbList>
                <BreadcrumbItem>
                  <span>01</span>
                </BreadcrumbItem>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbPage>
                    {session === mainSession
                      ? "Agent console"
                      : kind === "build"
                        ? "Skill builder"
                        : "Isolated execution"}
                  </BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
            <Typography variant="H1">
              {session === mainSession ? "Agent workbench" : title}
            </Typography>
            {session === mainSession && (
              <p className="chat-subtitle">
                Build skills. Automate work. Keep the context.
              </p>
            )}
          </div>
          <div className="session-actions">
            <Button
              ref={diagnosticsTrigger}
              onClick={() => setDiagnosticsOpen(true)}
            >
              <Terminal size={14} /> Diagnostics
            </Button>
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
        <Dialog open={diagnosticsOpen} onOpenChange={setDiagnosticsOpen}>
          <DialogContent
            className="memory-dialog"
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              diagnosticsTrigger.current?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>Execution diagnostics</DialogTitle>
              <DialogDescription>
                Recent tool failures, execution phases and runtime output.
                Failures before Python starts may have no script logs.
              </DialogDescription>
            </DialogHeader>
            <DialogBody>
              {diagnosticText ? (
                <pre className="execution-log-output">{diagnosticText}</pre>
              ) : (
                <Typography variant="MUTED">
                  No tool failures or runtime logs recorded for this session.
                </Typography>
              )}
            </DialogBody>
          </DialogContent>
        </Dialog>
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
            history.onScroll();
            const nearBottom =
              !history.hasNewer &&
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
                <div className="welcome-tagline-space" aria-hidden="true" />
                <h2>What should your agent learn?</h2>
                <p>
                  Build a skill, connect an API, or turn a recurring task into a
                  routine.
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
            {history.hasOlder && (
              <div className="history-edge">Scroll up for earlier messages</div>
            )}
            <div className="history-window">
              {history.items.map((item, index) => {
                return (
                  <div
                    id={`trace-${item.id}`}
                    key={item.id}
                    data-index={history.start + index}
                    data-history-id={item.id}
                    className="history-row"
                  >
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
                              {new Date(
                                item.createdAt * 1000,
                              ).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
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
                );
              })}
            </div>
            {history.hasNewer && (
              <div className="history-edge">Scroll down for newer messages</div>
            )}
            {pendingInteractions(session, allJobs).map(
              ({ job: j, pending }) => (
                <div key={j.id} className="human-panel">
                  <p className="eyebrow">WAITING FOR YOU</p>
                  {j.session_id !== session && (
                    <Button onClick={() => open(j.session_id)}>
                      Requested by a child execution · Open execution
                    </Button>
                  )}
                  {pending.map((p: RecordData) => (
                    <HumanRequest
                      key={p._request_id || p.call_id}
                      job={j}
                      pending={p}
                      refresh={refreshJobs}
                      onError={onError}
                    />
                  ))}
                </div>
              ),
            )}
          </div>
        </div>
        {showLatest && (
          <Button className="jump-latest" onClick={jumpToLatest}>
            ↓ Latest messages
          </Button>
        )}
        <div className="composer-area">
          <LiveActivity feed={activityFeed} jobs={allJobs} open={open} />
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
        timeline={timeline}
        onJump={(id) => {
          followLatest.current = false;
          setShowLatest(true);
          history.goToId(id);
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
            Execution {statusLabel(e.type).toLowerCase()}.{" "}
            <span>Show details</span>
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
        <Logo className="execution-avatar" />
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
    return <LocalFilePreview url={e.payload.url} title={e.payload.title} />;
  if (e.type === "progress") return null;
  return (
    <details className="tool-event">
      <summary>
        <Wrench size={14} />
        <code>{e.payload.tool_name || "Tool call"}</code>
        <span className="tool-meta">
          {e.payload.tool_call_id && (
            <UsageBadge jobId={e.job_id} toolCallId={e.payload.tool_call_id} />
          )}
          <DebugId id={String(e.id)} label="event" />
          {(e.payload.version_id || e.payload.result?.version_id) && (
            <DebugId
              id={e.payload.version_id || e.payload.result.version_id}
              label="v"
            />
          )}
          <Status
            status={
              e.type === "tool_begin"
                ? e.payload.interrupted_status || "running"
                : e.payload.result?.error || e.payload.result?.success === false
                  ? "failed"
                  : e.payload.mode === "wait"
                    ? "waiting"
                    : "completed"
            }
          />
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
      {e.payload.interrupted_status && (
        <p className="error">
          Execution {e.payload.interrupted_status} before this tool recorded a
          final result. Review execution diagnostics before retrying; effects
          may have occurred.
        </p>
      )}
      {e.type === "tool_end" &&
      ["read", "write", "edit", "bash"].includes(e.payload.tool_name) &&
      typeof e.payload.result?.content === "string" ? (
        <>
          <pre>{e.payload.result.content}</pre>
          {e.payload.result.diff && <pre>{e.payload.result.diff}</pre>}
          {e.payload.result.error && (
            <p className="error">{e.payload.result.error}</p>
          )}
          {e.payload.result.exit_code !== undefined && (
            <small>
              Exit {e.payload.result.exit_code} ·{" "}
              {e.payload.result.wall_time_seconds ?? 0}s
            </small>
          )}
        </>
      ) : (
        <pre>
          {JSON.stringify(
            e.type === "tool_begin" ? e.payload.args : e.payload.result,
            null,
            2,
          )}
        </pre>
      )}
    </details>
  );
}
createRoot(document.getElementById("root")!).render(
  <>
    <ApprovalKeyDialog />
    <App />
  </>,
);
