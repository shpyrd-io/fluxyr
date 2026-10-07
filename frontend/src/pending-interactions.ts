import type { RecordData } from "./api";

// A credential discovered by an isolated builder must remain answerable in the
// parent workbench. Only traverse real persisted parent links, never unrelated jobs.
export function pendingInteractions(
  session: string,
  jobs: RecordData[],
): { job: RecordData; pending: RecordData[] }[] {
  const reachable = new Set(
    jobs.filter((j) => j.session_id === session).map((j) => j.id),
  );
  let changed = true;
  while (changed) {
    changed = false;
    for (const job of jobs) {
      if (!reachable.has(job.id) && reachable.has(job.input?.parent_job_id)) {
        reachable.add(job.id);
        changed = true;
      }
    }
  }
  return jobs
    .filter((j) => j.status === "waiting" && reachable.has(j.id))
    .map((job) => ({
      job,
      pending: (job.pending || []).filter(
        (p: RecordData) =>
          p._status === "parked" &&
          !p._decision &&
          (job.session_id === session || p.name === "manage_vault_credential"),
      ),
    }))
    .filter((group) => group.pending.length);
}
