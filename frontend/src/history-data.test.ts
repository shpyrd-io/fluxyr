import test from "node:test";
import assert from "node:assert/strict";
import { mergeHistoryEvents, mergeMessages } from "./history-data.ts";
import { buildTimeline } from "./timeline.ts";

const ev = (id: number, text: string) => ({
  id,
  job_id: "j",
  created_at: id,
  type: "delta",
  payload: { block_id: "b", text },
});

test("snapshot replaces raw fragments and preserves concurrently received events", () => {
  const live = [ev(1, "A"), ev(2, "B"), ev(3, "C")];
  const merged = mergeHistoryEvents(live, {
    range_start: 0,
    range_end: 2,
    events: [ev(1, "AB")],
  });
  assert.equal(buildTimeline([], merged)[0].text, "ABC");
  assert.deepEqual(
    mergeHistoryEvents(merged, {
      range_start: 0,
      range_end: 2,
      events: [ev(1, "AB")],
    }),
    merged,
  );
});

test("older pages join partial blocks in order without duplicating boundary messages", () => {
  const latest = [ev(10, "world")];
  const merged = mergeHistoryEvents(latest, {
    range_start: 0,
    range_end: 9,
    events: [ev(1, "Hello ")],
  });
  assert.equal(buildTimeline([], merged)[0].text, "Hello world");
  const message = { id: "m", content: "hi", created_at: 1 };
  assert.equal(mergeMessages([message], [message]).length, 1);
});

test("page beginning inside a finished answer does not duplicate its persisted message", () => {
  const messages = [
    {
      id: "m",
      job_id: "j",
      role: "assistant",
      created_at: 20,
      content: "Hello world",
    },
  ];
  const events = [ev(10, "world"), { ...ev(11, ""), type: "stream_close" }];
  const timeline = buildTimeline(messages, events);
  assert.equal(timeline.length, 1);
  assert.equal(timeline[0].text, "Hello world");
});
