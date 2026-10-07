import { Status } from "./status";
import { useMemo, useEffect, useState, useRef, useLayoutEffect } from "react";
import { mainSession, type RecordData } from "./api";
import { Button, DebugId } from "./components";
import { Spinner } from "./ui/spinner/spinner";
import { activityRows } from "./live-activity-data";
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
  events,
  jobs,
  open,
}: {
  events: RecordData[];
  jobs: RecordData[];
  open: (id: string) => void;
}) {
  const rows = useMemo(() => activityRows(events, jobs), [events, jobs]);
  const active = rows.filter((r) => r.active);
  const visible = active.length ? active : rows.slice(-4);
  const [now, setNow] = useState(Date.now() / 1000);
  const scroll = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [expanded, setExpanded] = useState(true);
  useLayoutEffect(() => {
    if (following && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [rows, following, expanded]);
  useEffect(() => {
    if (!active.length) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [active.length]);
  if (!rows.length) return null;
  const content = (
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
        {visible.map((r) => (
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
              <UsageBadge jobId={r.jobId} />
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
              title="Recent stream fragments and tool calls, in order. Total counts above include the full execution."
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
  );
  return (
    <section className="live-activity" aria-label="Live execution activity">
      <Button
        className="activity-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "▾" : "▸"}{" "}
        {active.length ? "Live execution activity" : "Last execution activity"}
        {!expanded && active.length > 0 && <Spinner size="SM" />}
      </Button>
      {expanded && content}
    </section>
  );
}
