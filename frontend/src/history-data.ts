import type { RecordData } from "./api.ts";

// Replace just the snapshot's ID interval. Events that arrived concurrently
// beyond its cursor survive; compact fragments never double-count live deltas.
export function mergeHistoryEvents(old: RecordData[], page: RecordData) {
  return [
    ...old.filter((e) => e.id <= page.range_start || e.id > page.range_end),
    ...page.events,
  ].sort((a, b) => a.id - b.id);
}
export function mergeMessages(old: RecordData[], incoming: RecordData[]) {
  return [
    ...new Map([...old, ...incoming].map((m) => [m.id, m])).values(),
  ].sort((a, b) => a.created_at - b.created_at);
}
