import { test } from "node:test";
import assert from "node:assert/strict";
import { buildTimeline } from "./timeline.ts";

const event = (id: number, type: string, payload: Record<string, unknown>) => ({
  id,
  type,
  payload,
  job_id: "job",
  created_at: id,
});

test("a skill build updates its original card on completion, including replay and out-of-order delivery", () => {
  const start = event(1, "build_started", {
    job_id: "child",
    session_id: "builder",
    prompt: "Build skill: Weather",
    status: "queued",
  });
  const end = {
    ...event(4, "execution_report", {
      session_id: "builder",
      prompt: "Build Python actions…",
      status: "succeeded",
    }),
    job_id: "child",
  };
  for (const events of [
    [start, end, end],
    [end, start],
  ]) {
    const cards = buildTimeline([], events);
    assert.equal(cards.length, 1);
    assert.equal(cards[0].id, "build:child");
    assert.equal(cards[0].createdAt, 1);
    assert.equal(cards[0].event?.payload.status, "succeeded");
    assert.equal(cards[0].event?.payload.prompt, "Build skill: Weather");
    assert.equal(cards[0].event?.payload.job_id, "child");
  }
});

test("separate rebuilds and ordinary execution reports remain separate", () => {
  const cards = buildTimeline(
    [],
    [
      event(1, "build_started", {
        job_id: "build1",
        prompt: "Build skill: Weather",
        status: "queued",
      }),
      {
        ...event(2, "execution_report", { status: "failed" }),
        job_id: "build1",
      },
      event(3, "build_started", {
        job_id: "build2",
        prompt: "Build skill: Weather",
        status: "queued",
      }),
      {
        ...event(4, "execution_report", { status: "succeeded" }),
        job_id: "routine",
      },
    ],
  );
  assert.equal(cards.length, 3);
  assert.equal(cards[0].event?.payload.status, "failed");
  assert.equal(cards[1].event?.payload.status, "queued");
  assert.equal(cards[2].event?.type, "execution_report");
});

test("a routine is visible immediately and its completion updates the same card on replay", () => {
  const start = event(1, "execution_started", {
    job_id: "routine",
    session_id: "execution",
    prompt: "Check weather",
    status: "queued",
  });
  const end = {
    ...event(2, "execution_report", { status: "succeeded" }),
    job_id: "routine",
  };
  for (const events of [
    [start, end, end],
    [end, start],
  ]) {
    const cards = buildTimeline([], events);
    assert.equal(cards.length, 1);
    assert.equal(cards[0].event?.type, "execution_started");
    assert.equal(cards[0].event?.payload.status, "succeeded");
    assert.equal(cards[0].event?.payload.session_id, "execution");
  }
});

test("live text is visible without a job poll; reasoning, tools and answers keep their order", () => {
  const events = [
    event(2, "reasoning", { block_id: "r", text: "Inspect input" }),
    event(3, "stream_close", { block_id: "r" }),
    event(4, "delta", { block_id: "first", text: "I will inspect it." }),
    event(5, "tool_begin", { tool_call_id: "t", tool_name: "read_file" }),
    event(6, "tool_end", {
      tool_call_id: "t",
      tool_name: "read_file",
      result: { text: "input" },
    }),
    event(7, "delta", { block_id: "final", text: "**Complete**" }),
  ];
  const messages = [
    { id: "u", job_id: "job", role: "user", content: "Inspect", created_at: 1 },
  ];
  const live = buildTimeline(messages, events);
  assert.deepEqual(
    live.map((i) => i.kind),
    ["user", "reasoning", "assistant", "event", "assistant"],
  );
  assert.equal(live[4].live, true);
  assert.equal(live[3].event?.type, "tool_end");
  assert.equal(live[3].createdAt, 5);
  messages.push({
    id: "a",
    job_id: "job",
    role: "assistant",
    content: "**Complete**",
    created_at: 9,
  });
  events.push(event(8, "succeeded", {}));
  const replay = buildTimeline(messages, [...events, events[6]]);
  assert.equal(replay.filter((i) => i.text === "**Complete**").length, 1);
  assert.equal(replay.at(-1)?.live, false);
  assert.equal(replay.length, live.length);
});

test("long streams and paused partial text are retained", () => {
  const events = Array.from({ length: 1800 }, (_, i) =>
    event(i, "delta", { block_id: "long", text: "x" }),
  );
  events.push(event(1801, "paused", {}));
  const items = buildTimeline([], events);
  assert.equal(items[0].text.length, 1800);
  assert.equal(items[0].live, false);
});

test("legacy streams split at tools and do not merge separate replies", () => {
  const items = buildTimeline(
    [],
    [
      event(1, "delta", { text: "First" }),
      event(2, "delta", { text: " reply" }),
      event(3, "tool_end", { tool_call_id: "t", tool_name: "read_file" }),
      event(4, "delta", { text: "Final" }),
      event(5, "waiting", {}),
    ],
  );
  assert.deepEqual(
    items.filter((i) => i.kind === "assistant").map((i) => i.text),
    ["First reply", "Final"],
  );
  assert.ok(items.every((i) => !i.live));
});

test("terminal errors stay at their execution position and replay does not duplicate them", () => {
  const failed = event(2, "failed", { error: "Provider failed" });
  const items = buildTimeline(
    [
      {
        id: "new",
        job_id: "next",
        role: "user",
        content: "Continue",
        created_at: 3,
      },
    ],
    [failed, failed],
  );
  assert.equal(items.length, 2);
  assert.equal(items[0].event?.payload.error, "Provider failed");
  assert.equal(items[1].text, "Continue");
});
