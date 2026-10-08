import type { RecordData } from "./api.ts";
import { buildTimeline, type TimelineItem } from "./timeline.ts";
export type FlowNode = {
  id: string;
  jobId: string;
  kind: string;
  label: string;
  status: string;
  target?: string;
  children?: FlowNode[];
  inferred?: boolean;
  modelCallId?: string;
  toolCallId?: string;
  versionId?: string;
};
const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
export function executionFlow(
  messages: RecordData[],
  events: RecordData[],
  jobs: RecordData[] = [],
  timeline?: TimelineItem[],
): FlowNode[] {
  const ordered = [...new Map(events.map((e) => [e.id, e])).values()].sort(
    (a, b) => a.id - b.id,
  );
  timeline ??= buildTimeline(messages, ordered);
  const groups = new Map<string, { ids: string[]; inferred: boolean }>(),
    membership = new Map<string, string>();
  const legacy = new Map<
    string,
    { active: Set<string>; ids: string[]; id: string }
  >();
  const status = new Map<string, string>();
  const current = new Map(jobs.map((j) => [j.id, j.status]));
  const answered = new Set(
    ordered
      .filter((e) => e.type === "decision")
      .map((e) => `${e.job_id}:tool:${e.payload.call_id}`),
  );
  for (const e of ordered) {
    const p = e.payload;
    if (terminal.has(e.type)) status.set(e.job_id, e.type);
    if (e.type === "tool_batch_start" && p.parallel) {
      const id = `${e.job_id}:batch:${p.batch_id}`;
      const ids = p.calls.map(
        (c: RecordData) => `${e.job_id}:tool:${c.tool_call_id}`,
      );
      groups.set(id, { ids, inferred: false });
      ids.forEach((key: string) => membership.set(key, id));
    }
    if (["tool_begin", "tool_end"].includes(e.type) && !p.batch_id) {
      const key = `${e.job_id}:tool:${p.tool_call_id || e.id}`;
      if (e.type === "tool_begin") {
        let group = legacy.get(e.job_id);
        if (!group || !group.active.size) {
          group = { active: new Set(), ids: [], id: `overlap:${e.id}` };
          legacy.set(e.job_id, group);
        }
        group.active.add(key);
        group.ids.push(key);
        if (group.ids.length > 1) {
          groups.set(group.id, { ids: group.ids, inferred: true });
          group.ids.forEach((k) => membership.set(k, group!.id));
        }
      } else legacy.get(e.job_id)?.active.delete(key);
    }
  }
  const incomingByJob = new Map(
    ordered
      .filter((e) => ["webhook_received", "worker_received"].includes(e.type))
      .map((e) => [e.job_id, e]),
  );
  function node(item: TimelineItem): FlowNode | null {
    const p = item.event?.payload || {};
    const incoming =
      item.kind === "user" ? incomingByJob.get(item.jobId) : undefined;
    if (incoming)
      return {
        id: item.id,
        jobId: item.jobId,
        kind: incoming.type === "worker_received" ? "worker_event" : "webhook",
        versionId: incoming.payload.listener_version_id,
        label: `Receipt #${incoming.payload.receipt_id} · ${incoming.payload.session_key}`,
        status: "completed",
        target: item.id,
      };
    if (item.kind !== "event")
      return {
        id: item.id,
        jobId: item.jobId,
        kind: item.kind,
        label: item.text.replace(/\s+/g, " ").slice(0, 120) || "…",
        status: item.live ? "running" : "completed",
        target: item.id,
        modelCallId: item.modelCallId,
      };
    if (["tool_begin", "tool_end"].includes(item.event!.type))
      return {
        id: item.id,
        jobId: item.jobId,
        kind: p.tool_name === "ask_human" ? "human_request" : "tool",
        label: p.tool_name || "Tool call",
        versionId: p.version_id || p.result?.version_id,
        toolCallId: p.tool_call_id,
        status:
          p.result?.error || p.result?.success === false
            ? "error"
            : p.mode === "wait"
              ? p.result?.__human__
                ? current.get(item.jobId) === "waiting" ||
                  !current.has(item.jobId)
                  ? "waiting"
                  : terminal.has(current.get(item.jobId)!)
                    ? current.get(item.jobId)!
                    : "running"
                : answered.has(item.id)
                  ? "completed"
                  : "waiting"
              : item.event!.type === "tool_end"
                ? "completed"
                : status.get(item.jobId) || "running",
        target: item.id,
      };
    if (item.event!.type === "build_started")
      return {
        id: item.id,
        jobId: item.jobId,
        kind: "build",
        label: p.prompt,
        status: current.get(item.jobId) || p.status,
        target: item.id,
      };
    if (["failed", "interrupted"].includes(item.event!.type))
      return {
        id: item.id,
        jobId: item.jobId,
        kind: "error",
        label: p.error || item.event!.type,
        status: "error",
        target: item.id,
      };
    if (["execution_report", "execution_started"].includes(item.event!.type))
      return {
        id: item.id,
        jobId: item.jobId,
        kind: "execution",
        label: p.prompt || "Execution result",
        status: p.status,
        target: item.id,
      };
    return null;
  }
  const decisions = ordered
    .filter((e) => e.type === "decision")
    .map((e) => ({
      id: `decision:${e.id}`,
      jobId: e.job_id,
      createdAt: e.created_at,
      kind: "event" as const,
      text: "",
      event: e,
    }));
  const nodes = [...timeline, ...decisions]
    .sort((a, b) => a.createdAt - b.createdAt)
    .map((i) =>
      i.event?.type === "decision"
        ? {
            id: i.id,
            jobId: i.jobId,
            kind: "human_response",
            label:
              i.event.payload.result?.answer ||
              i.event.payload.reason ||
              i.event.payload.decision,
            status: "completed",
          }
        : node(i),
    )
    .filter((n): n is FlowNode => !!n);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const rendered = new Set<string>();
  const result: FlowNode[] = [];
  for (const n of nodes) {
    const groupId = membership.get(n.id);
    if (!groupId) {
      result.push(n);
      continue;
    }
    if (rendered.has(groupId)) continue;
    rendered.add(groupId);
    const group = groups.get(groupId)!;
    const children = group.ids.map(
      (id) =>
        byId.get(id) || {
          id,
          jobId: n.jobId,
          kind: "tool",
          label: "Tool queued",
          status: "queued",
        },
    );
    const settled = children.every(
      (c) => !["running", "queued", "waiting"].includes(c.status),
    );
    result.push({
      id: groupId,
      jobId: n.jobId,
      kind: "parallel",
      label: "Parallel tools",
      status: settled
        ? children.some((c) =>
            ["error", "failed", "interrupted"].includes(c.status),
          )
          ? "failed"
          : children.some((c) => c.status === "cancelled")
            ? "cancelled"
            : "completed"
        : children.some((c) => c.status === "waiting")
          ? "waiting"
          : "running",
      children,
      inferred: group.inferred,
    });
  }
  return result;
}
