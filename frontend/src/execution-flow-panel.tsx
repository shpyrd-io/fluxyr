import { useHistoryWindow } from "./use-history-window";
import type { TimelineItem } from "./timeline";
import { SelectField, SelectOption } from "./form-select";
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
import { Button, DebugId } from "./components";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardFooter,
} from "./ui/card/card";
import { Typography } from "./ui/typography/typography";
import { Status } from "./status";
import { executionFlow, type FlowNode } from "./execution-flow";
import type { RecordData } from "./api";
import { UsageBadge } from "./usage";
const labels: Record<string, string> = {
  webhook: "Webhook event",
  user: "User input",
  assistant: "Agent answer",
  reasoning: "Reasoning",
  tool: "Tool call",
  human_request: "Ask human",
  human_response: "Human response",
  build: "Skill build",
  error: "Execution failed",
  execution: "Execution",
};
const icons: Record<string, typeof Wrench> = {
  webhook: GitBranch,
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
  timeline,
}: {
  timeline: TimelineItem[];
  messages: RecordData[];
  events: RecordData[];
  jobs: RecordData[];
  onJump: (id: string) => void;
}) {
  const all = useMemo(
    () => executionFlow(messages, events, jobs, timeline),
    [messages, events, jobs, timeline],
  );
  const [filter, setFilter] = useState("all"),
    [following, setFollowing] = useState(true);
  const scroll = useRef<HTMLDivElement>(null);
  const jobIds = [...new Set(all.map((n) => n.jobId).filter(Boolean))];
  const nodes = useMemo(
    () => (filter === "all" ? all : all.filter((n) => n.jobId === filter)),
    [all, filter],
  );
  const history = useHistoryWindow(nodes, scroll, () => following);
  useEffect(() => {
    if (following) history.goToLatest();
  }, [following, history.goToLatest]);
  function renderNode(n: FlowNode) {
    const Icon = icons[n.kind] || Wrench;
    return (
      <Card key={n.id} className={`flow-node ${n.kind} ${n.status}`}>
        <CardHeader className="flow-card-header">
          <CardTitle className="flow-node-kind">
            <Icon size={12} />
            {labels[n.kind] || n.kind}
          </CardTitle>
          <Status status={n.status} />
        </CardHeader>
        <CardContent className="flow-card-content">
          <button
            type="button"
            className="flow-node-link"
            onClick={() => n.target && onJump(n.target)}
            disabled={!n.target}
            title={n.label}
            aria-label={`${labels[n.kind] || n.kind}: ${n.label}`}
          >
            <span className="flow-node-label">{n.label}</span>
          </button>
          {n.versionId && <DebugId id={n.versionId} label="v" />}
          <UsageBadge
            callId={n.modelCallId}
            toolCallId={n.toolCallId}
            jobId={
              n.toolCallId || ["build", "execution"].includes(n.kind)
                ? n.jobId
                : undefined
            }
          />
        </CardContent>
      </Card>
    );
  }
  return (
    <aside className="execution-flow" aria-label="Execution sequence">
      <header>
        <div>
          <GitBranch size={16} />
          <Typography variant="H4" as="h2">
            Execution sequence
          </Typography>
        </div>
        <p>Recorded events · live updates</p>
        <SelectField
          aria-label="Filter timeline by execution"
          value={filter}
          onValueChange={(value) => {
            setFilter(value);
            setFollowing(true);
          }}
        >
          <SelectOption value="all">All executions</SelectOption>
          {jobIds.map((id, i) => (
            <SelectOption value={id} key={id}>
              Execution {i + 1} · {id.slice(0, 8)}
            </SelectOption>
          ))}
        </SelectField>
      </header>
      <div
        className="flow-scroll"
        ref={scroll}
        onScroll={() => {
          const el = scroll.current!;
          history.onScroll();
          setFollowing(
            !history.hasNewer &&
              el.scrollHeight - el.scrollTop - el.clientHeight < 50,
          );
        }}
      >
        {history.hasOlder && (
          <div className="history-edge">Scroll up for earlier events</div>
        )}
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
            {history.items.map((n, index) => {
              return (
                <li
                  key={n.id}
                  data-history-id={n.id}
                  data-index={history.start + index}
                  aria-posinset={history.start + index + 1}
                  aria-setsize={nodes.length}
                  className={`${n.children ? "flow-parallel" : "flow-step"} ${history.start + index === nodes.length - 1 ? "flow-last" : ""}`}
                >
                  {n.children ? (
                    <Card
                      className="flow-batch"
                      aria-label={`${n.children.length} parallel calls`}
                    >
                      <CardHeader className="flow-card-header">
                        <CardTitle className="flow-node-kind">
                          <GitBranch size={12} />
                          {n.children.length} parallel calls
                        </CardTitle>
                        <Status status={n.status} />
                        {n.inferred && (
                          <span title="Reconstructed from overlapping tool start/end events">
                            {" "}
                            · observed
                          </span>
                        )}
                      </CardHeader>
                      <CardContent className="flow-batch-content">
                        <div className="flow-branches">
                          {n.children.map(renderNode)}
                        </div>
                      </CardContent>
                      <CardFooter className={`flow-junction join ${n.status}`}>
                        {n.status === "completed" ? (
                          <>
                            <Check size={12} />
                            Joined
                          </>
                        ) : n.status === "waiting" ? (
                          "Waiting for input"
                        ) : n.status === "failed" ? (
                          "Joined with failures"
                        ) : n.status === "cancelled" ? (
                          "Cancelled"
                        ) : (
                          "Waiting for branches"
                        )}
                      </CardFooter>
                    </Card>
                  ) : (
                    renderNode(n)
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {history.hasNewer && (
          <div className="history-edge">Scroll down for newer events</div>
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
