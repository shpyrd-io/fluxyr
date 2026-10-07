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
test("explicit parallel batches retain branches even when individual calls finish before the next begins", () => {
  const events = [
    e(1, "tool_batch_start", {
      batch_id: "b",
      parallel: true,
      calls: [{ tool_call_id: "a" }, { tool_call_id: "b" }],
    }),
    e(2, "tool_begin", {
      tool_call_id: "a",
      tool_name: "read_file",
      batch_id: "b",
    }),
    e(3, "tool_end", {
      tool_call_id: "a",
      tool_name: "read_file",
      batch_id: "b",
      mode: "continue",
    }),
    e(4, "tool_begin", {
      tool_call_id: "b",
      tool_name: "search_files",
      batch_id: "b",
    }),
    e(5, "tool_end", {
      tool_call_id: "b",
      tool_name: "search_files",
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
        tool_name: "read_file",
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
