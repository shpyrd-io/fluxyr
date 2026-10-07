import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCron, toCron, describeCron } from "./cron.ts";
test("visual schedules round-trip cron, including zero hours and weekday lists", () => {
  for (const [cron, mode] of [
    ["", "manual"],
    ["* * * * *", "minutes"],
    ["*/15 * * * *", "minutes"],
    ["7 * * * *", "hourly"],
    ["30 */6 * * *", "hourly"],
    ["0 0 * * *", "daily"],
    ["15 8 * * 1,3,5", "weekly"],
    ["0 9 31 * *", "monthly"],
  ]) {
    const parsed = parseCron(cron);
    assert.equal(parsed.mode, mode);
    assert.equal(toCron(parsed), cron);
  }
});
test("ranges, names and Sunday 7 convert without changing weekday semantics", () => {
  assert.equal(toCron(parseCron("0 8 * * MON-FRI")), "0 8 * * 1,2,3,4,5");
  assert.equal(toCron(parseCron("0 8 * * 1-5")), "0 8 * * 1,2,3,4,5");
  assert.equal(toCron(parseCron("0 8 * * 7")), "0 8 * * 0");
  assert.equal(describeCron("0 8 * * 1-5"), "Weekdays at 08:00");
});
test("advanced expressions are never approximated or overwritten", () => {
  for (const cron of [
    "*/7 * * * *",
    "0 8 1 * MON",
    "0 8 * JAN MON",
    "0 8 L * *",
    "0 0 * * 1#2",
    "0 8 * * 6-1",
    "0 8 * * * *",
    "not cron",
  ]) {
    assert.equal(parseCron(cron).mode, "custom");
    assert.equal(toCron(parseCron(cron)), cron);
  }
});
test("an empty weekday selection or invalid time cannot serialize as a different schedule", () => {
  assert.equal(toCron({ ...parseCron("0 8 * * 1"), days: [] }), null);
  assert.equal(toCron({ ...parseCron("0 8 * * *"), time: "" }), null);
  assert.equal(toCron({ ...parseCron("0 8 * * *"), time: "25:00" }), null);
});
