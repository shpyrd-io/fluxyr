import assert from "node:assert/strict";
import { test } from "node:test";
import { createPoller } from "./polling.ts";

const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

test("bursts coalesce and never overlap requests", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 10_000 });
  let calls = 0,
    active = 0,
    maximum = 0;
  let resolve!: () => void;
  const poller = createPoller(async () => {
    calls++;
    active++;
    maximum = Math.max(maximum, active);
    await new Promise<void>((r) => {
      resolve = r;
    });
    active--;
  });
  poller.start();
  await flush();
  for (let i = 0; i < 200; i++) void poller.refresh();
  t.mock.timers.tick(1000);
  await flush();
  assert.equal(calls, 1);
  resolve();
  await flush();
  t.mock.timers.tick(999);
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  await flush();
  assert.equal(calls, 2);
  assert.equal(maximum, 1);
  poller.stop();
  resolve();
  await flush();
  t.mock.timers.tick(60000);
  await flush();
  assert.equal(calls, 2);
});

test("idle fallback is thirty seconds and hidden tabs do not fetch", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 10_000 });
  let calls = 0,
    visible = true;
  const poller = createPoller(
    async () => {
      calls++;
    },
    30000,
    2000,
    () => visible,
  );
  poller.start();
  await flush();
  t.mock.timers.tick(29999);
  await flush();
  assert.equal(calls, 1);
  visible = false;
  t.mock.timers.tick(60001);
  await flush();
  assert.equal(calls, 1);
  visible = true;
  void poller.refresh();
  await flush();
  assert.equal(calls, 2);
  poller.stop();
});
