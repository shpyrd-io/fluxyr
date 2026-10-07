export const HISTORY_PAGE_SIZE = 30;
export const HISTORY_WINDOW_SIZE = 90;
export const HISTORY_EDGE_PX = 160;

export type HistoryBounds = { start: number; end: number };

export function historyPage(
  bounds: HistoryBounds,
  total: number,
  direction: "older" | "newer",
): HistoryBounds {
  if (direction === "older") {
    const start = Math.max(0, bounds.start - HISTORY_PAGE_SIZE);
    return { start, end: Math.min(bounds.end, start + HISTORY_WINDOW_SIZE) };
  }
  const end = Math.min(total, bounds.end + HISTORY_PAGE_SIZE);
  return { start: Math.max(bounds.start, end - HISTORY_WINDOW_SIZE), end };
}

export function historyEdge(
  top: number,
  previousTop: number,
  height: number,
  viewport: number,
) {
  if (top < previousTop && top <= HISTORY_EDGE_PX) return "older";
  if (top > previousTop && height - top - viewport <= HISTORY_EDGE_PX)
    return "newer";
  return null;
}
