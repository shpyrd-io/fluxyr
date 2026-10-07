import { Status } from "./status";
import {
  useEffect,
  useState,
  useRef,
  useLayoutEffect,
  useSyncExternalStore,
} from "react";
import { mainSession, type RecordData } from "./api";
import { Button, DebugId } from "./components";
import { Spinner } from "./ui/spinner/spinner";
import { ActivityFeed } from "./live-activity-data";
import { UsageBadge } from "./usage";
const phases: Record<string, string> = {
  waiting_model: "Waiting for model response",
  reasoning: "Receiving reasoning",
  generating_arguments: "Generating arguments",
  streaming: "Receiving stream",
  tool: "Executing tool",
  tool_finished: "Tool returned",
  model_finished: "Model response received",
};
function duration(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  return value < 60 ? `${value}s` : `${Math.floor(value / 60)}m ${value % 60}s`;
}
export function LiveActivity({
  feed,
  jobs,
  open,
}: {
  feed: ActivityFeed;
  jobs: RecordData[];
  open: (id: string) => void;
}) {
  const rows = useSyncExternalStore(feed.subscribe, feed.getSnapshot);
  useEffect(() => feed.setJobs(jobs), [feed, jobs]);
  const [now, setNow] = useState(Date.now() / 1000);
  const scroll = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [expanded, setExpanded] = useState(true);
  useLayoutEffect(() => {
    if (following && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [rows, following, expanded]);
  useEffect(() => {
    if (!rows.length || !expanded) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [rows.length, expanded]);
  if (!rows.length) return null;
  const content = expanded ? (
    <>
      <div className="activity-legend">
        <span>. current stream</span>
        <span>🛠️ tool</span>
        <span>• child stream</span>
      </div>
      <div
        className="activity-lines"
        ref={scroll}
        onScroll={() => {
          const el = scroll.current!;
          setFollowing(el.scrollHeight - el.scrollTop - el.clientHeight < 35);
        }}
      >
        {rows.map((r) => (
          <div
            className={"activity-line " + (r.active ? "live" : "finished")}
            key={r.key}
          >
            <div className="activity-source">
              {r.active && ["running", "building"].includes(r.status) && (
                <Spinner size="SM" aria-label="Activity in progress" />
              )}
              <Button
                className="activity-origin"
                onClick={() => open(r.sessionId)}
                title={r.label}
              >
                {r.depth
                  ? `↳ ${r.label}`
                  : r.sessionId === mainSession
                    ? "Workbench"
                    : r.label}
                {r.scope !== "main" ? " · " + (r.tool || "subcall") : ""}
              </Button>
              <DebugId id={r.jobId} />
              <UsageBadge
                jobId={r.scope === "main" ? r.jobId : undefined}
                callId={r.scope !== "main" ? r.scope : undefined}
              />
              <Status status={r.status} />
            </div>
            <div className="activity-phase">
              {r.active &&
              ["reasoning", "streaming", "generating_arguments"].includes(
                r.phase,
              ) &&
              now - r.at > 5
                ? "Waiting for next provider fragment"
                : phases[r.phase] || r.phase}
              {r.tool && r.scope === "main" ? ` · ${r.tool}` : ""}
              <span>
                {r.chunks.toLocaleString()} chunks ·{" "}
                {r.characters.toLocaleString()} chars
              </span>
            </div>
            <div className="activity-phase">
              <span>
                Elapsed{" "}
                {duration(
                  (r.finishedAt ?? (r.active ? now : r.at)) - r.startedAt,
                )}
                {r.active ? ` · Last activity ${duration(now - r.at)} ago` : ""}
              </span>
            </div>
            <div
              className="activity-pulses"
              aria-label={`${r.chunks} stream fragments and ${r.tools} tool calls`}
              title="Live fragments and tool calls received since opening this view. Older activity is not replayed."
            >
              {r.markers ? (
                r.markers.split(/(•+)/).map((part, i) => (
                  <span
                    key={i}
                    className={
                      part.startsWith("•") ? "child-pulses" : undefined
                    }
                  >
                    {part}
                  </span>
                ))
              ) : (
                <span className="activity-placeholder">Awaiting stream…</span>
              )}
            </div>
          </div>
        ))}
      </div>
      {!following && (
        <Button className="activity-follow" onClick={() => setFollowing(true)}>
          ↓ Latest activity
        </Button>
      )}
    </>
  ) : null;
  return (
    <section className="live-activity" aria-label="Live execution activity">
      <Button
        className="activity-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "▾" : "▸"} Live execution activity
        {!expanded && <Spinner size="SM" />}
      </Button>
      {expanded && content}
    </section>
  );
}
