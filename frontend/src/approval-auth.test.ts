import { test } from "node:test";
import assert from "node:assert/strict";
import { isApprovalPath } from "./approval-auth.ts";

test("approval keys are scoped to dedicated and legacy approval routes", () => {
  for (const path of [
    "/approvals",
    "/approvals?x=1",
    "/approvals/j/r/assets/0",
    "/approvals/j/r/decision",
    "/jobs/j/decisions/c",
    "/jobs/j/vault/r",
  ]) {
    assert.equal(isApprovalPath(path), true, path);
  }
  for (const path of [
    "/settings",
    "/vault",
    "/jobs",
    "/approvals-other",
    "/files/content?path=/approvals",
    "/jobs/j/vault",
    "https://other.test/approvals",
  ]) {
    assert.equal(isApprovalPath(path), false, path);
  }
});
