import { useMemo, useEffect, useRef, useState } from "react";
import {
  GitBranch,
  UserRound,
  BrainCircuit,
  MessageSquare,
  Wrench,
  Hand,
  Boxes,
  ArrowDown,
  Check,
  AlertCircle,
} from "lucide-react";
import { Button } from "./components";
import { Spinner } from "./ui/spinner/spinner";
import { executionFlow, type FlowNode } from "./execution-flow";
import type { RecordData } from "./api";
import { UsageBadge } from "./usage";
const labels: Record<string, string> = {
  user: "User input",
  assistant: "Agent answer",
  reasoning: "Reasoning",
  tool: "Tool call",
  human_request: "Ask human",
  human_response: "Human response",
  build: "Skill build",
  error: "Error",
  execution: "Execution",
};
const icons: Record<string, typeof Wrench> = {
  user: UserRound,
  assistant: MessageSquare,
  reasoning: BrainCircuit,
  tool: Wrench,
  human_request: Hand,
  human_response: UserRound,
  build: Boxes,
  error: AlertCircle,
};
export function ExecutionFlowPanel({
  messages,
  events,
  jobs,
  onJump,
}: {
  messages: RecordData[];
  events: RecordData[];
  jobs: RecordData[];
  onJump: (id: string) => void;
}) {
  const all = useMemo(
    () => executionFlow(messages, events, jobs),
    [messages, events, jobs],
  );
  const [filter, setFilter] = useState("all"),
    [following, setFollowing] = useState(true);
  const scroll = useRef<HTMLDivElement>(null);
  const jobIds = [...new Set(all.map((n) => n.jobId).filter(Boolean))];
  const nodes = filter === "all" ? all : all.filter((n) => n.jobId === filter);
  useEffect(() => {
    if (following && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [all, following, filter]);
  function renderNode(n: FlowNode) {
    const Icon = icons[n.kind] || Wrench;
    return (
      <button
        key={n.id}
        className={`flow-node ${n.kind} ${n.status}`}
        onClick={() => n.target && onJump(n.target)}
        disabled={!n.target}
        title={n.label}
      >
        <span className="flow-node-kind">
          <Icon size={12} />
          {labels[n.kind] || n.kind}
          {n.status === "running" ? (
            <Spinner size="SM" />
          ) : ["error", "failed", "interrupted"].includes(n.status) ? (
            <AlertCircle size={12} />
          ) : ["completed", "succeeded"].includes(n.status) ? (
            <Check size={11} />
          ) : (
            <small>{n.status}</small>
          )}
        </span>
        <span className="flow-node-label">{n.label}</span>
        <UsageBadge
          callId={n.modelCallId}
          jobId={["build", "execution"].includes(n.kind) ? n.jobId : undefined}
        />
      </button>
    );
  }
  return (
    <aside className="execution-flow" aria-label="Execution timeline">
      <header>
        <div>
          <GitBranch size={16} />
          <h2>Execution timeline</h2>
        </div>
        <p>Recorded events · live updates</p>
        <select
          aria-label="Filter timeline by execution"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All executions</option>
          {jobIds.map((id, i) => (
            <option value={id} key={id}>
              Execution {i + 1} · {id.slice(0, 8)}
            </option>
          ))}
        </select>
      </header>
      <div
        className="flow-scroll"
        ref={scroll}
        onScroll={() => {
          const el = scroll.current!;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 50);
        }}
      >
        {!nodes.length ? (
          <div className="flow-empty">
            <GitBranch size={25} />
            <p>
              The execution path will appear here as the conversation
              progresses.
            </p>
          </div>
        ) : (
          <ol className="flow-path">
            {nodes.map((n) => (
              <li
                key={n.id}
                className={n.children ? "flow-parallel" : "flow-step"}
              >
                {n.children ? (
                  <>
                    <div className="flow-junction">
                      <GitBranch size={12} />
                      {n.children.length} parallel calls
                      {n.inferred && (
                        <span title="Reconstructed from overlapping tool start/end events">
                          {" "}
                          · observed
                        </span>
                      )}
                    </div>
                    <div className="flow-branches">
                      {n.children.map(renderNode)}
                    </div>
                    <div className={`flow-junction join ${n.status}`}>
                      {n.status === "completed" ? (
                        <>
                          <Check size={12} />
                          Joined
                        </>
                      ) : n.status === "waiting" ? (
                        "Waiting for input"
                      ) : (
                        "Waiting for branches"
                      )}
                    </div>
                  </>
                ) : (
                  renderNode(n)
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
      {!following && (
        <Button className="flow-follow" onClick={() => setFollowing(true)}>
          <ArrowDown size={12} />
          Latest events
        </Button>
      )}
    </aside>
  );
}
