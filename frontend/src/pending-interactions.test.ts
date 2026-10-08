import { test } from "node:test";
import assert from "node:assert/strict";
import { pendingInteractions } from "./pending-interactions.ts";

const pending = (name: string, call_id: string) => ({
  name,
  call_id,
  _status: "parked",
});
test("workbench shows its Vault cards and descendant builder cards without unrelated executions", () => {
  const jobs = [
    { id: "main", session_id: "workbench", status: "building" },
    {
      id: "child",
      session_id: "builder",
      status: "waiting",
      input: { parent_job_id: "main" },
      pending: [
        pending("manage_vault_credential", "c"),
        pending("browser_request_input", "private"),
        pending("ask_human", "h"),
      ],
    },
    {
      id: "grandchild",
      session_id: "sub",
      status: "waiting",
      input: { parent_job_id: "child" },
      pending: [pending("manage_vault_credential", "gc")],
    },
    {
      id: "unrelated",
      session_id: "routine",
      status: "waiting",
      pending: [pending("manage_vault_credential", "u")],
    },
  ];
  assert.deepEqual(
    pendingInteractions("workbench", jobs).flatMap((g) =>
      g.pending.map((p) => p.call_id),
    ),
    ["c", "private", "gc"],
  );
  assert.deepEqual(
    pendingInteractions("builder", jobs).flatMap((g) =>
      g.pending.map((p) => p.call_id),
    ),
    ["c", "private", "h", "gc"],
  );
});
test("resolved cards disappear while undecided siblings remain", () => {
  const jobs = [
    {
      id: "j",
      session_id: "s",
      status: "waiting",
      pending: [
        {
          ...pending("manage_vault_credential", "a"),
          _decision: { decision: "complete" },
        },
        pending("manage_vault_credential", "b"),
      ],
    },
  ];
  assert.deepEqual(
    pendingInteractions("s", jobs)[0].pending.map((p) => p.call_id),
    ["b"],
  );
});
