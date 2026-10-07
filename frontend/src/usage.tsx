import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, type RecordData } from "./api";
const UsageContext = createContext<RecordData>({});
export function UsageProvider({ children }: { children: ReactNode }) {
  const [usage, setUsage] = useState<RecordData>({});
  useEffect(() => {
    let disposed = false;
    const refresh = () =>
      api("/usage")
        .then((v) => {
          if (!disposed) setUsage(v);
        })
        .catch(() => {});
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);
  return (
    <UsageContext.Provider value={usage}>{children}</UsageContext.Provider>
  );
}
function cost(u?: RecordData) {
  if (!u?.requests || u.missing_cost === u.requests) return "Cost —";
  return `${u.missing_cost ? "≥ " : ""}$${u.cost_usd.toFixed(6)}`;
}
export function UsageBadge({
  jobId,
  callId,
  toolCallId,
  total = false,
}: {
  jobId?: string;
  callId?: string;
  toolCallId?: string;
  total?: boolean;
}) {
  const usage = useContext(UsageContext);
  const u = total
    ? usage.total
    : toolCallId
      ? usage.by_tool?.[jobId || ""]?.[toolCallId]
      : callId
        ? usage.by_call?.[callId]
        : jobId
          ? usage.by_job?.[jobId]
          : null;
  if (!u && !total) return null;
  const explanation = total
    ? "Instance total since usage tracking began, including child executions and memory."
    : toolCallId
      ? "Provider-reported usage for model requests made by this tool. Excludes the agent response that called it."
      : callId
        ? "Usage for the model request that generated this block, shared with other blocks from that request. Do not add these repeated values."
        : "Execution usage, including its child executions and memory.";
  return (
    <span
      className={total ? "usage-total" : "usage-badge"}
      title={`${explanation} ${u ? `Input: ${u.input_tokens}; output: ${u.output_tokens}; cached input: ${u.cached_tokens}. ${u.missing_cost} requests without reported cost.` : "No recorded usage yet."}`}
    >
      {!total &&
        u &&
        (u.missing_tokens === u.requests
          ? "Tokens — · "
          : `${u.missing_tokens ? "≥ " : ""}${u.tokens.toLocaleString()} tokens · `)}
      {total ? (u?.cost_usd ? `$${u.cost_usd.toFixed(6)}` : "$0.00") : cost(u)}
    </span>
  );
}
