import type { RecordData } from "./api.ts";

// Errors before Python starts have no stdout/progress events. Their persisted
// tool result is still diagnostic evidence, including after a page reload.
export function executionDiagnostics(events: RecordData[]): string {
  const entries: string[] = [];
  const seen = new Set<number>();
  const progressCalls = new Set(
    events
      .filter((e) => e.type === "progress")
      .map((e) => `${e.job_id}:${e.payload.call_id}`),
  );
  const text = (value: unknown) =>
    (typeof value === "string" ? value : JSON.stringify(value) || "").slice(
      0,
      12000,
    );
  for (const event of events) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    const p = event.payload || {};
    const call = p.tool_call_id || p.call_id;
    const prefix = `[job ${event.job_id || "unknown"} / event ${event.id}${call ? " / call " + call : ""}]`;
    if (event.type === "progress") {
      entries.push(`${prefix} ${text(p.text || "")}`);
    } else if (event.type === "tool_end") {
      const result = p.result || {};
      const failed =
        !!result.error || result.success === false || result.passed === false;
      if (failed) {
        const details = [
          p.tool_name || "Tool call",
          "FAILED",
          result.phase && `phase: ${result.phase}`,
          result.executed === false && "Python/tool was not executed",
          result.executed === true && "Python/tool started",
          result.error_type || result.type,
          result.exit_code !== undefined && `exit code: ${result.exit_code}`,
          (result.version_id || p.version_id) &&
            `version: ${result.version_id || p.version_id}`,
        ].filter(Boolean);
        entries.push(
          `${prefix} ${details.join(" · ")}\n${text(result.error || "Tool reported failure without an error message")}${result.remediation ? "\n" + text(result.remediation) : ""}`,
        );
      }
      // Historical results can contain logs even when no progress events exist.
      if (
        !progressCalls.has(`${event.job_id}:${call}`) &&
        Array.isArray(result.logs)
      ) {
        for (const line of result.logs.slice(-100))
          entries.push(`${prefix} ${text(line)}`);
      }
    } else if (["failed", "interrupted"].includes(event.type)) {
      const test = p.outcome?.test;
      entries.push(
        `${prefix} ${event.type.toUpperCase()}\n${text(p.error || test?.error || p.outcome?.failures || p.message || "Execution stopped without an error message")}`,
      );
      if (test?.phase)
        entries.push(
          `${prefix} phase: ${test.phase}${test.executed === false ? " · Python/tool was not executed" : ""}${test.version_id ? " · version: " + test.version_id : ""}`,
        );
    }
  }
  return entries.slice(-200).join("\n\n");
}
