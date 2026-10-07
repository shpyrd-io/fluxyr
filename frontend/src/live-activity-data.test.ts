import { test } from "node:test";
import assert from "node:assert/strict";
import { activityRows } from "./live-activity-data.ts";
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
test("nested completion stops pulsing while the parent continues and a large stream has bounded markers", () => {
  const rows = activityRows(
    [
      event(1, { scope: "techdoc", depth: 2, chunks: 20000 }),
      event(2, {
        scope: "techdoc",
        depth: 2,
        phase: "model_finished",
        chunks: 0,
      }),
    ],
    [{ id: "child", status: "running" }],
  );
  assert.equal(rows[0].chunks, 20000);
  assert.ok(rows[0].markers.length <= 4000);
  assert.equal(rows[0].startedAt, 1);
  assert.equal(rows[0].finishedAt, 2);
  assert.equal(rows[0].active, false);
});
