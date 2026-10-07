import type { RecordData } from "./api.ts";
export type ActivityRow = {
  key: string;
  jobId: string;
  sessionId: string;
  scope: string;
  depth: number;
  label: string;
  phase: string;
  tool?: string;
  chunks: number;
  characters: number;
  tools: number;
  markers: string;
  at: number;
  startedAt: number;
  finishedAt?: number;
  status: string;
  active: boolean;
};
const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

// A bounded live projection, independent of chat history and execution diagrams.
export class ActivityFeed {
  private rows = new Map<string, ActivityRow>();
  private jobs = new Map<string, RecordData>();
  private cursor = 0;
  private snapshot: ActivityRow[] = [];
  private listeners = new Set<() => void>();
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish() {
    this.snapshot = [...this.rows.values()]
      .filter((r) => r.active)
      .sort((a, b) => a.at - b.at);
    this.listeners.forEach((listener) => listener());
  }
  setJobs(jobs: RecordData[]) {
    this.jobs = new Map(jobs.map((j) => [j.id, j]));
    let changed = false;
    for (const [key, row] of this.rows) {
      const job = this.jobs.get(row.jobId);
      if (terminal.has(job?.status)) {
        this.rows.delete(key);
        changed = true;
      } else if (
        job &&
        (row.status !== job.status ||
          (row.scope === "main" &&
            job.started_at &&
            row.startedAt !== job.started_at))
      ) {
        this.rows.set(key, {
          ...row,
          status: job.status,
          active: true,
          startedAt:
            row.scope === "main"
              ? job.started_at || row.startedAt
              : row.startedAt,
        });
        changed = true;
      }
    }
    if (changed) this.publish();
  }
  update(events: RecordData[]) {
    let changed = false;
    for (const e of events) {
      if (e.id <= this.cursor) continue;
      this.cursor = e.id;
      const p = e.payload;
      if (
        terminal.has(e.type) ||
        (e.type === "execution_report" && terminal.has(p.status))
      ) {
        for (const [key, row] of this.rows) {
          if (row.jobId === e.job_id) {
            this.rows.delete(key);
            changed = true;
          }
        }
        continue;
      }
      if (e.type !== "activity") continue;
      const job = this.jobs.get(p.origin_job_id);
      if (terminal.has(job?.status)) continue;
      const key = `${p.origin_job_id}:${p.scope}`;
      if (p.scope !== "main" && p.phase === "model_finished") {
        changed = this.rows.delete(key) || changed;
        continue;
      }
      const previous = this.rows.get(key);
      const row: ActivityRow = previous
        ? { ...previous }
        : {
            key,
            jobId: p.origin_job_id,
            sessionId: p.origin_session_id,
            scope: p.scope,
            depth: p.depth,
            label: p.label,
            phase: p.phase,
            chunks: 0,
            characters: 0,
            tools: 0,
            markers: "",
            at: e.created_at,
            startedAt:
              p.scope === "main"
                ? job?.started_at || e.created_at
                : e.created_at,
            status: job?.status || "unknown",
            active: !!job,
          };
      row.chunks += p.chunks || 0;
      row.characters += p.characters || 0;
      const tool = p.phase === "tool";
      if (tool) row.tools++;
      row.markers = Array.from(
        row.markers +
          (tool
            ? "🛠️"
            : (p.depth > 0 || p.scope !== "main" ? "•" : ".").repeat(
                Math.min(4000, p.chunks || 0),
              )),
      )
        .slice(-4000)
        .join("")
        .replace(/^\uFE0F/, "");
      row.phase = p.phase;
      row.tool = p.tool;
      row.at = e.created_at;
      this.rows.set(key, row);
      changed = true;
    }
    if (changed) this.publish();
  }
}
