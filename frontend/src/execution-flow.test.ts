import { test } from "node:test";
import assert from "node:assert/strict";
import { executionFlow } from "./execution-flow.ts";
const e = (id: number, type: string, payload: Record<string, any> = {}) => ({
  id,
  type,
  payload,
  job_id: "job",
  created_at: id,
});
test("tool cards use their own usage identity rather than the shared model request", () => {
  const flow = executionFlow([], [
    e(1, "tool_begin", {tool_call_id: "docs", tool_name: "tech_doc", model_call_id: "parent"}),
    e(2, "tool_end", {tool_call_id: "docs", tool_name: "tech_doc", model_call_id: "parent", mode: "continue"}),
  ]);
  assert.equal(flow[0].jobId, "job");
  assert.equal(flow[0].toolCallId, "docs");
  assert.equal(flow[0].modelCallId, undefined);
});
test("explicit parallel batches retain branches even when individual calls finish before the next begins", () => {
  const events = [
    e(1, "tool_batch_start", {
      batch_id: "b",
      parallel: true,
      calls: [{ tool_call_id: "a" }, { tool_call_id: "b" }],
    }),
    e(2, "tool_begin", {
      tool_call_id: "a",
      tool_name: "read",
      batch_id: "b",
    }),
    e(3, "tool_end", {
      tool_call_id: "a",
      tool_name: "read",
      batch_id: "b",
      mode: "continue",
    }),
    e(4, "tool_begin", {
      tool_call_id: "b",
      tool_name: "bash",
      batch_id: "b",
    }),
    e(5, "tool_end", {
      tool_call_id: "b",
      tool_name: "bash",
      batch_id: "b",
      mode: "continue",
    }),
  ];
  const flow = executionFlow([], events);
  assert.equal(flow.length, 1);
  assert.equal(flow[0].kind, "parallel");
  assert.equal(flow[0].status, "completed");
  assert.equal(flow[0].children?.length, 2);
  assert.equal(flow[0].inferred, false);
  assert.deepEqual(flow, executionFlow([], [...events, ...events]));
});
test("historical overlap is reconstructed but sequential calls are never labelled parallel", () => {
  const begin = (id: number, call: string) =>
    e(id, "tool_begin", { tool_call_id: call, tool_name: call });
  const end = (id: number, call: string) =>
    e(id, "tool_end", {
      tool_call_id: call,
      tool_name: call,
      mode: "continue",
    });
  const concurrent = executionFlow(
    [],
    [begin(1, "a"), begin(2, "b"), end(3, "b"), end(4, "a")],
  );
  assert.equal(concurrent[0].inferred, true);
  assert.equal(concurrent[0].children?.length, 2);
  const sequential = executionFlow(
    [],
    [begin(1, "a"), end(2, "a"), begin(3, "b"), end(4, "b")],
  );
  assert.equal(sequential.length, 2);
  assert.ok(sequential.every((n) => n.kind === "tool"));
});
test("human responses are represented and unresolved branches do not show a join", () => {
  const flow = executionFlow(
    [],
    [
      e(1, "tool_batch_start", {
        batch_id: "b",
        parallel: true,
        calls: [{ tool_call_id: "a" }, { tool_call_id: "b" }],
      }),
      e(2, "tool_begin", {
        tool_call_id: "a",
        tool_name: "read",
        batch_id: "b",
      }),
      e(3, "decision", { result: { answer: "Celsius" }, decision: "complete" }),
    ],
  );
  assert.equal(flow[0].status, "running");
  assert.equal(flow[0].children?.[1].status, "queued");
  assert.equal(flow[1].kind, "human_response");
  assert.equal(flow[1].label, "Celsius");
});

test("human decisions settle parked tool nodes and child build status uses the durable job", () => {
  const flow = executionFlow(
    [],
    [
      e(1, "tool_end", {
        tool_call_id: "q",
        tool_name: "ask_human",
        mode: "wait",
      }),
      e(2, "decision", {
        call_id: "q",
        decision: "complete",
        result: { answer: "Celsius" },
      }),
      e(3, "build_started", {
        job_id: "child",
        status: "queued",
        prompt: "Build skill",
      }),
    ],
    [{ id: "child", status: "running" }],
  );
  assert.equal(flow[0].status, "completed");
  assert.equal(flow[1].kind, "human_response");
  assert.equal(flow[2].status, "running");
});

test("a replied round does not complete an action awaiting its next human input", () => {
  const events = [
    e(1, "tool_begin", { tool_call_id: "a", tool_name: "action_review" }),
    e(2, "tool_end", {
      tool_call_id: "a",
      tool_name: "action_review",
      mode: "wait",
      result: { __human__: {} },
    }),
    e(3, "decision", {
      call_id: "a",
      request_id: "round-1",
      decision: "complete",
      result: { answer: "Blue" },
    }),
    e(4, "tool_end", {
      tool_call_id: "a",
      tool_name: "action_review",
      mode: "wait",
      result: { __human__: {} },
    }),
  ];
  const waiting = executionFlow([], events, [{ id: "job", status: "waiting" }]);
  assert.equal(waiting.find((n) => n.kind === "tool")?.status, "waiting");
  const finished = executionFlow(
    [],
    [
      ...events,
      e(5, "tool_end", {
        tool_call_id: "a",
        tool_name: "action_review",
        mode: "continue",
        result: { success: true },
      }),
    ],
    [{ id: "job", status: "succeeded" }],
  );
  assert.equal(finished.filter((n) => n.kind === "tool").length, 1);
  assert.equal(finished.find((n) => n.kind === "tool")?.status, "completed");
});

test("failed branch marks the parallel container failed and retains executed version", () => {
  const flow = executionFlow([], [
    e(1, "tool_batch_start", {batch_id: "b", parallel: true, calls: [{tool_call_id: "a"}, {tool_call_id: "b"}]}),
    e(2, "tool_end", {batch_id: "b", tool_call_id: "a", tool_name: "action_roll", mode: "continue", result: {success: true, version_id: "v1"}}),
    e(3, "tool_end", {batch_id: "b", tool_call_id: "b", tool_name: "action_roll", mode: "continue", result: {success: false, error: "Failed", version_id: "v1"}}),
  ]);
  assert.equal(flow[0].status, "failed");
  assert.deepEqual(flow[0].children?.map(n => n.versionId), ["v1", "v1"]);
});

test("reactive execution input is identified by its persisted webhook receipt", () => {
  const flow = executionFlow(
    [
      {
        id: "message",
        job_id: "job",
        role: "user",
        content: "Incoming event",
        created_at: 1,
      },
    ],
    [e(2, "webhook_received", { receipt_id: 42, session_key: "customer-1" })],
  );
  assert.equal(flow[0].kind, "webhook");
  assert.equal(flow[0].label, "Receipt #42 · customer-1");
});
