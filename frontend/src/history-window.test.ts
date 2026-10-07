import assert from "node:assert/strict";
import test from "node:test";
import {
  historyEdge,
  historyPage,
  HISTORY_WINDOW_SIZE,
} from "./history-window.ts";

test("history only pages toward a loaded edge, never in the middle or on stationary scroll", () => {
  assert.equal(historyEdge(900, 1000, 5000, 600), null);
  assert.equal(historyEdge(159, 200, 5000, 600), "older");
  assert.equal(historyEdge(159, 100, 5000, 600), null);
  assert.equal(historyEdge(159, 159, 5000, 600), null);
  assert.equal(historyEdge(4300, 4200, 5000, 600), "newer");
});

test("paging preserves overlap, bounds DOM size, and can revisit both ends of a long history", () => {
  const total = 10000;
  let bounds = { start: total - 30, end: total };
  while (bounds.start > 0) {
    const next = historyPage(bounds, total, "older");
    assert.ok(next.end - next.start <= HISTORY_WINDOW_SIZE);
    assert.ok(next.start < bounds.start && next.end > bounds.start);
    bounds = next;
  }
  while (bounds.end < total) {
    const next = historyPage(bounds, total, "newer");
    assert.ok(next.end - next.start <= HISTORY_WINDOW_SIZE);
    assert.ok(next.end > bounds.end && next.start < bounds.end);
    bounds = next;
  }
  assert.equal(bounds.end, total);
});
