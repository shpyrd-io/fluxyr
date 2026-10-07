import type { RecordData } from "./api.ts";

export type TimelineItem = {
  id: string;
  jobId: string;
  createdAt: number;
  kind: "user" | "assistant" | "reasoning" | "event";
  text: string;
  live?: boolean;
  event?: RecordData;
  modelCallId?: string;
};

// Build the same timeline from a live stream or replay. Do not gate visible text
// on the separately polled job list, which can lag behind the stream.
export function buildTimeline(
  messages: RecordData[],
  events: RecordData[],
): TimelineItem[] {
  const items: TimelineItem[] = [];
  const blocks = new Map<string, TimelineItem>();
  const tools = new Map<string, TimelineItem>();
  const legacy = new Map<string, string>();
  const seen = new Set<number>();
  const builds = new Map<string, TimelineItem>();
  const buildIds = new Set(
    events
      .filter((e) => ["build_started", "execution_started"].includes(e.type))
      .map((e) => e.payload.job_id),
  );
  for (const e of events) {
    if (seen.has(e.id)) continue;
    seen.add(e.id);
    const p = e.payload;
    if (["stream_open", "delta", "reasoning"].includes(e.type)) {
      let key = p.block_id && `${e.job_id}:${p.block_id}`;
      if (!key) {
        key = legacy.get(e.job_id) || `legacy:${e.id}`;
        legacy.set(e.job_id, key);
      }
      let block = blocks.get(key);
      if (!block) {
        block = {
          id: key,
          jobId: e.job_id,
          modelCallId: p.model_call_id,
          createdAt: e.created_at,
          kind:
            e.type === "reasoning" || p.kind === "reasoning"
              ? "reasoning"
              : "assistant",
          text: "",
          live: true,
        };
        blocks.set(key, block);
        items.push(block);
      }
      block.text += p.text || "";
    } else if (e.type === "stream_close") {
      const block = blocks.get(`${e.job_id}:${p.block_id}`);
      if (block) block.live = false;
    } else if (["tool_begin", "tool_end"].includes(e.type)) {
      legacy.delete(e.job_id);
      const key = `${e.job_id}:tool:${p.tool_call_id || e.id}`;
      const existing = tools.get(key);
      if (existing) {
        // Deferred build completion may omit the originating model request.
        // Retain its identity while replacing the tool result and status.
        existing.event = {
          ...e,
          payload: { ...existing.event!.payload, ...p },
        };
      } else {
        const item: TimelineItem = {
          id: key,
          jobId: e.job_id,
          createdAt: e.created_at,
          kind: "event",
          text: "",
          event: e,
        };
        tools.set(key, item);
        items.push(item);
      }
    } else if (
      ["build_started", "execution_started"].includes(e.type) ||
      (e.type === "execution_report" && buildIds.has(e.job_id))
    ) {
      const isStart = ["build_started", "execution_started"].includes(e.type);
      const childId = isStart ? p.job_id : e.job_id;
      const existing = builds.get(childId);
      if (existing) {
        if (isStart) {
          existing.createdAt = e.created_at;
          existing.event = {
            ...e,
            payload: { ...p, ...existing.event!.payload, prompt: p.prompt },
          };
        } else {
          existing.event = {
            ...existing.event,
            payload: { ...existing.event!.payload, status: p.status },
          };
        }
      } else {
        const item: TimelineItem = {
          id: `build:${childId}`,
          jobId: childId,
          createdAt: e.created_at,
          kind: "event",
          text: "",
          event: {
            ...e,
            type: isStart
              ? e.type
              : events.find(
                  (start) =>
                    ["build_started", "execution_started"].includes(
                      start.type,
                    ) && start.payload.job_id === childId,
                )!.type,
            payload: { ...p, job_id: childId },
          },
        };
        builds.set(childId, item);
        items.push(item);
      }
    } else if (
      ["progress", "preview", "execution_report", "build_started"].includes(
        e.type,
      )
    ) {
      items.push({
        id: `event:${e.id}`,
        jobId: e.job_id,
        createdAt: e.created_at,
        kind: "event",
        text: "",
        event: e,
      });
    } else if (
      [
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
        "paused",
        "waiting",
      ].includes(e.type)
    ) {
      if (e.type === "failed" || e.type === "interrupted") {
        const prior = items.find((i) => i.id === `failure:${e.job_id}`);
        if (prior) prior.event = e;
        else
          items.push({
            id: `failure:${e.job_id}`,
            jobId: e.job_id,
            createdAt: e.created_at,
            kind: "event",
            text: "",
            event: e,
          });
      }
      legacy.delete(e.job_id);
      for (const block of blocks.values())
        if (block.jobId === e.job_id) block.live = false;
    }
  }
  for (const m of messages) {
    // The final persisted message is also the final streamed text block. Keep
    // intermediate model text and reasoning, but don't duplicate the final reply.
    if (
      m.role === "assistant" &&
      items.some(
        (i) =>
          i.jobId === m.job_id &&
          i.kind === "assistant" &&
          i.text.trim() === m.content.trim(),
      )
    )
      continue;
    items.push({
      id: `message:${m.id}`,
      jobId: m.job_id,
      createdAt: m.created_at,
      kind: m.role === "user" ? "user" : "assistant",
      text: m.content,
    });
  }
  return items.sort((a, b) => a.createdAt - b.createdAt);
}
