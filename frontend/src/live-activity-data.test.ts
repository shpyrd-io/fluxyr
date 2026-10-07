import { test } from "node:test";
import assert from "node:assert/strict";
import { ActivityFeed } from "./live-activity-data.ts";
function activityRows(events: any[], jobs: any[]) {
  const feed = new ActivityFeed();
  feed.setJobs(jobs);
  feed.update(events);
  return feed.getSnapshot();
}
const event = (id: number, extra: Record<string, unknown> = {}) => ({
  id,
  type: "activity",
  created_at: id,
  payload: {
    origin_job_id: "child",
    origin_session_id: "session",
    scope: "main",
    depth: 1,
    label: "Builder",
    phase: "streaming",
    chunks: 3,
    characters: 9,
    ...extra,
  },
});
test("activity distinguishes tools and nested streams, counts fragments, and deduplicates replay", () => {
  const events = [
    event(1),
    event(2, {
      phase: "tool",
      tool: "create_action",
      chunks: 0,
      characters: 0,
    }),
    event(3, { scope: "techdoc", depth: 2 }),
  ];
  const rows = activityRows(
    [...events, ...events],
    [{ id: "child", status: "running" }],
  );
  assert.equal(rows.length, 2);
  assert.equal(rows[0].markers, "•••🛠️");
  assert.equal(rows[0].chunks, 3);
  assert.equal(rows[0].tools, 1);
  assert.equal(rows[1].markers, "•••");
  assert.equal(
    activityRows(
      [event(1, { depth: 0 })],
      [{ id: "child", status: "running" }],
    )[0].markers,
    "...",
  );
  assert.equal(rows[0].active, true);
  assert.ok(
    activityRows(events, [{ id: "child", status: "succeeded" }]).every(
      (r) => !r.active,
    ),
  );
});
test("nested completion removes its marks while the parent continues", () => {
  const feed = new ActivityFeed();
  feed.setJobs([{ id: "child", status: "running" }]);
  feed.update([
    event(1),
    event(2, { scope: "techdoc", depth: 2, chunks: 20000 }),
  ]);
  const nested = feed.getSnapshot()[1];
  assert.equal(nested.chunks, 20000);
  assert.ok(nested.markers.length <= 4000);
  feed.update([
    event(3, { scope: "techdoc", phase: "model_finished", chunks: 0 }),
  ]);
  assert.deepEqual(
    feed.getSnapshot().map((r) => r.scope),
    ["main"],
  );
});
test("terminal stream events clear marks before the next job poll", () => {
  for (const type of ["succeeded", "failed", "cancelled", "interrupted"]) {
    const feed = new ActivityFeed();
    feed.setJobs([{ id: "child", status: "running" }]);
    feed.update([event(1)]);
    assert.equal(feed.getSnapshot().length, 1);
    feed.update([{ id: 2, job_id: "child", type, payload: {} }]);
    assert.deepEqual(feed.getSnapshot(), []);
    feed.update([event(1)]);
    assert.deepEqual(feed.getSnapshot(), []);
  }
});
test("completed job polls clear child activity even without a mirrored terminal event", () => {
  const feed = new ActivityFeed();
  feed.setJobs([{ id: "child", status: "building" }]);
  feed.update([event(1)]);
  feed.setJobs([{ id: "child", status: "succeeded" }]);
  assert.deepEqual(feed.getSnapshot(), []);
  feed.update([event(2)]);
  assert.deepEqual(feed.getSnapshot(), []);
});
test("activity is accumulated incrementally and unrelated history does not notify subscribers", () => {
  const feed = new ActivityFeed();
  let updates = 0;
  feed.subscribe(() => updates++);
  feed.setJobs([{ id: "child", status: "running" }]);
  feed.update([event(1)]);
  const original = feed.getSnapshot();
  feed.update([{ id: 2, type: "delta", payload: { text: "Hello" } }]);
  assert.equal(feed.getSnapshot(), original);
  assert.equal(updates, 1);
  feed.update([event(3)]);
  assert.equal(feed.getSnapshot()[0].chunks, 6);
  assert.equal(original[0].chunks, 3);
  assert.equal(updates, 2);
});
