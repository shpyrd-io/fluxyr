import type { RecordData } from "./api.ts";

export type TextTail = { offset: number; end: number; text: string };
const MAX_TEXT = 65536;
const MAX_BLOCKS = 128;

export function mergeLiveText(text: string, end: number, tail?: TextTail) {
  // A missing prefix is never fabricated. The next durable batch fills gaps
  // after reconnect, queue overflow or joining a routine already in progress.
  if (!tail || tail.offset > end || tail.end <= end) return text;
  return text + tail.text.slice(end - tail.offset);
}

export class LiveTextFeed {
  private tails = new Map<string, TextTail>();
  private closed = new Set<string>();
  private listeners = new Map<string, Set<() => void>>();
  private dirty = new Set<string>();
  private frame: number | undefined;
  private schedule: (fn: FrameRequestCallback) => number;
  private cancel: (id: number) => void;
  constructor(
    schedule = (fn: FrameRequestCallback) => requestAnimationFrame(fn),
    cancel = (id: number) => cancelAnimationFrame(id),
  ) {
    this.schedule = schedule;
    this.cancel = cancel;
  }
  get(key: string) {
    return this.tails.get(key);
  }
  subscribe(key: string, listener: () => void) {
    let listeners = this.listeners.get(key);
    if (!listeners) this.listeners.set(key, (listeners = new Set()));
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.listeners.delete(key);
    };
  }
  private changed(key: string) {
    if (!this.listeners.has(key)) return;
    this.dirty.add(key);
    if (this.frame !== undefined) return;
    this.frame = this.schedule(() => {
      this.frame = undefined;
      const dirty = [...this.dirty];
      this.dirty.clear();
      for (const key of dirty) this.listeners.get(key)?.forEach((fn) => fn());
    });
  }
  append(event: RecordData) {
    const p = event.payload;
    if (!p || !["delta", "reasoning"].includes(event.type) || !p.block_id)
      return;
    const key = `${event.job_id}:${p.block_id}`;
    if (this.closed.has(key)) return;
    let { text_offset: offset, text_end: end, text } = p;
    if (
      !Number.isSafeInteger(offset) ||
      offset < 0 ||
      typeof text !== "string" ||
      end !== offset + text.length
    )
      return;
    const previous = this.tails.get(key);
    if (previous && end <= previous.end) return;
    if (previous && offset <= previous.end && offset >= previous.offset) {
      text = previous.text + text.slice(previous.end - offset);
      offset = previous.offset;
    }
    if (text.length > MAX_TEXT) {
      text = text.slice(-MAX_TEXT);
      // Do not retain half of a Unicode surrogate pair at the tail boundary.
      if (/^[\uDC00-\uDFFF]/u.test(text)) text = text.slice(1);
      offset = end - text.length;
    }
    this.tails.set(key, { offset, end, text });
    while (this.tails.size > MAX_BLOCKS) {
      const oldest = this.tails.keys().next().value!;
      this.tails.delete(oldest);
      this.changed(oldest);
    }
    this.changed(key);
  }
  settle(events: RecordData[]) {
    for (const event of events) {
      const keys =
        event.type === "stream_close"
          ? [`${event.job_id}:${event.payload.block_id}`]
          : [
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "paused",
                "waiting",
              ].includes(event.type)
            ? [...this.tails.keys()].filter((key) =>
                key.startsWith(`${event.job_id}:`),
              )
            : [];
      for (const key of keys) {
        this.closed.add(key);
        this.tails.delete(key);
        this.changed(key);
      }
    }
    while (this.closed.size > MAX_BLOCKS * 2)
      this.closed.delete(this.closed.values().next().value!);
  }
  clear() {
    const keys = [...this.tails.keys()];
    this.tails.clear();
    this.closed.clear();
    for (const key of keys) this.changed(key);
  }
  dispose() {
    if (this.frame !== undefined) this.cancel(this.frame);
    this.frame = undefined;
    this.dirty.clear();
    this.tails.clear();
    this.closed.clear();
  }
}
