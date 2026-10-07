import { Status } from "./status";
import { Logo } from "./components";
import { useEffect, useState } from "react";
import { api, type RecordData } from "./api";
import { Button, DebugId } from "./components";
import { Spinner } from "./ui/spinner/spinner";
import { UsageBadge } from "./usage";

const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
// Paging a completed build back into view must not restart its loader/polling.
const progressCache = new Map<string, RecordData>();
const phases: Record<string, string> = {
  queued: "Waiting for the builder",
  planning: "Analyzing the skill and planning actions",
  researching: "Researching with",
  generating: "Generating Python for the planned actions",
  creating: "Creating Python actions",
  finalizing: "Actions created · finalizing the build",
  succeeded: "Build complete · candidates ready for testing",
  failed: "Build failed",
  cancelled: "Build cancelled",
  interrupted: "Build interrupted",
  paused: "Build paused",
  waiting: "Waiting for human input",
};

export function BuildProgress({
  event,
  open,
}: {
  event: RecordData;
  open: (id: string) => void;
}) {
  const jobId = event.payload.job_id;
  const [progress, setProgress] = useState<RecordData | null>(
    () => progressCache.get(jobId) || null,
  );
  const [error, setError] = useState("");
  useEffect(() => {
    const cached = progressCache.get(jobId);
    if (cached && terminal.has(cached.status)) {
      setProgress(cached);
      return;
    }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let finished = false;
      try {
        const next = await api(`/jobs/${jobId}/build`);
        progressCache.delete(jobId);
        progressCache.set(jobId, next);
        if (progressCache.size > 100)
          progressCache.delete(progressCache.keys().next().value!);
        if (disposed) return;
        setProgress(next);
        setError("");
        finished = terminal.has(next.status);
      } catch {
        if (!disposed) setError("Progress could not be refreshed. Retrying…");
      }
      if (!disposed && !finished) timer = setTimeout(refresh, 1500);
    }
    refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [jobId, event.payload.status]);

  const status = progress?.status || event.payload.status;
  const running = status === "running" && !progress?.control;
  let phase = progress
    ? phases[progress.phase] || progress.phase
    : "Loading build progress…";
  if (progress?.research_tools?.length)
    phase += ` ${progress.research_tools.join(", ")}`;
  if (status === "running" && progress?.control)
    phase =
      progress.control === "cancel" ? "Cancelling build…" : "Pausing build…";

  return (
    <section className="build-progress" aria-label="Skill build progress">
      <Button
        className="build-progress-header"
        onClick={() => open(progress?.session_id || event.payload.session_id)}
      >
        <Logo className="execution-avatar" />
        <strong>
          {progress
            ? `Build skill: ${progress.skill_name}`
            : event.payload.prompt}
        </strong>
        <span className="tool-meta">
          <UsageBadge jobId={jobId} />
          <DebugId id={jobId} />
          <Status status={status} />
        </span>
      </Button>
      <div className="build-progress-body">
        <div className="build-progress-phase" aria-live="polite">
          {running && <Spinner size="SM" aria-label="Building skill" />}
          <span>{phase}</span>
          {!!progress?.total && (
            <small>
              {progress.created}/{progress.total} actions created
            </small>
          )}
        </div>
        {!!progress?.actions.length && (
          <ul className="build-progress-actions">
            {progress.actions.map((action: RecordData) => (
              <li key={action.name}>
                <span
                  className={`build-action-state ${action.status}`}
                  aria-hidden="true"
                >
                  {action.status === "created"
                    ? "✓"
                    : action.status === "creating"
                      ? "↻"
                      : "·"}
                </span>
                <code title={action.description}>{action.name}</code>
                <small>{action.status}</small>
              </li>
            ))}
          </ul>
        )}
        {(error || progress?.error) && (
          <p className="build-progress-error">{error || progress?.error}</p>
        )}
      </div>
    </section>
  );
}
