import { test } from "node:test";
import assert from "node:assert/strict";
import { executionDiagnostics } from "./execution-diagnostics.ts";

const event = (id: number, type: string, payload: Record<string, unknown>) => ({
  id,
  type,
  payload,
  job_id: "routine-job",
});

test("pre-Python test failures remain visible without progress or stdout", () => {
  const value = executionDiagnostics([
    event(1, "tool_end", {
      tool_name: "test_action",
      tool_call_id: "test-call",
      result: {
        success: false,
        passed: false,
        logs: [],
        executed: false,
        phase: "credential_resolution",
        version_id: "candidate-version",
        error: "Credential could not be resolved",
        remediation: "Edit the Vault credential",
      },
      args: { private_input: "must-not-appear" },
    }),
  ]);
  for (const expected of [
    "test_action",
    "test-call",
    "routine-job",
    "event 1",
    "credential_resolution",
    "not executed",
    "candidate-version",
    "Credential could not be resolved",
    "Edit the Vault credential",
  ])
    assert.ok(value.includes(expected), expected);
  assert.ok(!value.includes("must-not-appear"));
});

test("ledger refusals and validation failures are visible without runner logs", () => {
  const value = executionDiagnostics([
    event(1, "tool_end", {
      tool_name: "test_action",
      result: {
        error: "Previous effect outcome is uncertain; automatic retry refused",
      },
    }),
    event(2, "tool_end", {
      tool_name: "test_action",
      result: {
        error: "Missing params",
        type: "validation_error",
        executed: false,
      },
    }),
  ]);
  assert.match(value, /automatic retry refused/);
  assert.match(value, /validation_error/);
  assert.match(value, /Missing params/);
});

test("uses persisted stdout as fallback and avoids duplicating streamed logs", () => {
  const done = event(2, "tool_end", {
    tool_call_id: "call",
    result: {
      success: false,
      error: "Script failed",
      exit_code: 1,
      logs: ["Traceback line"],
    },
  });
  assert.match(executionDiagnostics([done]), /Traceback line/);
  const output = executionDiagnostics([
    event(1, "progress", { call_id: "call", text: "Traceback line" }),
    done,
    done,
  ]);
  assert.equal(output.match(/Traceback line/g)?.length, 1);
  assert.equal(output.match(/Script failed/g)?.length, 1);
  assert.match(output, /exit code: 1/);
});

test("successful outputs and human waits are not reported as failures", () => {
  assert.equal(
    executionDiagnostics([
      event(1, "tool_end", {
        result: { success: true, output: { secret: "not-a-log" } },
      }),
      event(2, "tool_end", {
        mode: "wait",
        result: { success: null, waiting: {} },
      }),
    ]),
    "",
  );
});

test("isolated test jobs expose their persisted failure outcome", () => {
  const output = executionDiagnostics([
    event(1, "failed", {
      error: null,
      outcome: {
        test: {
          error: "Dependency setup failed",
          phase: "dependency_installation",
          executed: false,
          version_id: "candidate",
        },
      },
    }),
  ]);
  assert.match(output, /Dependency setup failed/);
  assert.match(output, /dependency_installation/);
  assert.match(output, /candidate/);
});
