import { Badge } from "./ui/badge/badge";

export function statusLabel(status: string) {
  if (["failed", "error"].includes(status)) return "Failed";
  if (["succeeded", "completed"].includes(status)) return "Completed";
  return status.replaceAll("_", " ");
}

export function Status({
  status = "queued",
  compact = false,
}: {
  status?: string;
  compact?: boolean;
}) {
  const normalized = status === "error" ? "failed" : status;
  const variant = ["running", "building"].includes(normalized)
    ? "SCANNING"
    : ["succeeded", "completed", "active", "passed"].includes(normalized)
      ? "ACTIVE"
      : ["failed", "interrupted"].includes(normalized)
        ? "CRITICAL"
        : ["paused", "waiting"].includes(normalized)
          ? "WARNING"
          : "OFFLINE";
  return (
    <Badge
      variant={variant}
      className={`status ${normalized}${compact ? " status-compact" : ""}`}
      title={compact ? statusLabel(normalized) : undefined}
      aria-label={compact ? statusLabel(normalized) : undefined}
    >
      {compact ? (
        <span className="sr-only">{statusLabel(normalized)}</span>
      ) : (
        statusLabel(normalized)
      )}
    </Badge>
  );
}
