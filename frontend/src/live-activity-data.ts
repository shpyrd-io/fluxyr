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
export function activityRows(
  events: RecordData[],
  jobs: RecordData[],
): ActivityRow[] {
  const current = new Map(jobs.map((j) => [j.id, j]));
  const rows = new Map<string, ActivityRow>();
  const seen = new Set<number>();
  for (const e of events) {
    if (e.type !== "activity" || seen.has(e.id)) continue;
    seen.add(e.id);
    const p = e.payload,
      key = `${p.origin_job_id}:${p.scope}`;
    let row = rows.get(key);
    if (!row) {
      row = {
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
            ? current.get(p.origin_job_id)?.started_at || e.created_at
            : e.created_at,
        finishedAt: current.get(p.origin_job_id)?.finished_at,
        status: current.get(p.origin_job_id)?.status || "unknown",
        active: false,
      };
      rows.set(key, row);
    }
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
    if (row.scope !== "main" && p.phase === "model_finished")
      row.finishedAt = e.created_at;
    row.active =
      !!current.get(row.jobId) &&
      !terminal.has(row.status) &&
      (row.scope === "main" || p.phase !== "model_finished");
  }
  return [...rows.values()].sort((a, b) => a.at - b.at);
}
