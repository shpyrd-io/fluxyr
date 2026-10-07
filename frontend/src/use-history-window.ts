import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import {
  HISTORY_PAGE_SIZE,
  HISTORY_WINDOW_SIZE,
  historyEdge,
  historyPage,
} from "./history-window";

type Cursor = { first: string; last: string };
type Anchor = { id: string; offset?: number; align?: "center" | "end" };

/** Render whole pages only at the loaded edges, retaining the visible DOM anchor. */
export function useHistoryWindow<T extends { id: string }>(
  items: T[],
  scroll: RefObject<HTMLDivElement | null>,
  following: () => boolean,
) {
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const pending = useRef<Anchor | null>(null);
  const previousTop = useRef(0);
  const lastIndex = cursor
    ? items.findIndex((item) => item.id === cursor.last)
    : -1;
  const end = following() || lastIndex < 0 ? items.length : lastIndex + 1;
  const firstIndex = cursor
    ? items.findIndex((item) => item.id === cursor.first)
    : -1;
  const start = Math.max(
    0,
    end - HISTORY_WINDOW_SIZE,
    firstIndex < 0 ? end - HISTORY_PAGE_SIZE : Math.min(firstIndex, end),
  );

  const rows = () =>
    Array.from(
      scroll.current?.querySelectorAll<HTMLElement>("[data-history-id]") || [],
    );
  const setBounds = useCallback(
    (first: number, last: number) => {
      if (items.length)
        setCursor({ first: items[first].id, last: items[last - 1].id });
    },
    [items],
  );

  useLayoutEffect(() => {
    const el = scroll.current;
    const target = pending.current;
    if (!el || !target) return;
    const row = rows().find((row) => row.dataset.historyId === target.id);
    if (!row) return;
    const relativeTop =
      row.getBoundingClientRect().top - el.getBoundingClientRect().top;
    if (target.align === "end") el.scrollTop = el.scrollHeight;
    else if (target.align === "center")
      el.scrollTop +=
        relativeTop - Math.max(0, (el.clientHeight - row.offsetHeight) / 2);
    else el.scrollTop += relativeTop - (target.offset || 0);
    previousTop.current = el.scrollTop;
    pending.current = null;
  });

  function onScroll() {
    const el = scroll.current;
    if (!el || pending.current) return;
    const direction = historyEdge(
      el.scrollTop,
      previousTop.current,
      el.scrollHeight,
      el.clientHeight,
    );
    previousTop.current = el.scrollTop;
    if (!cursor && items.length) setBounds(start, end);
    if (
      !direction ||
      (direction === "older" ? start === 0 : end === items.length)
    )
      return;
    const edge = el.getBoundingClientRect().top;
    const anchor = rows().find(
      (row) => row.getBoundingClientRect().bottom > edge,
    );
    if (anchor)
      pending.current = {
        id: anchor.dataset.historyId!,
        offset: anchor.getBoundingClientRect().top - edge,
      };
    const next = historyPage({ start, end }, items.length, direction);
    setBounds(next.start, next.end);
  }

  const goToLatest = useCallback(() => {
    if (!items.length) return;
    pending.current = { id: items[items.length - 1].id, align: "end" };
    setBounds(Math.max(0, items.length - HISTORY_PAGE_SIZE), items.length);
  }, [items, setBounds]);

  const goToId = useCallback(
    (id: string) => {
      const index = items.findIndex((item) => item.id === id);
      if (index < 0) return;
      pending.current = { id, align: "center" };
      const first = Math.max(0, index - Math.floor(HISTORY_PAGE_SIZE / 2));
      setBounds(first, Math.min(items.length, first + HISTORY_PAGE_SIZE));
    },
    [items, setBounds],
  );

  return {
    items: items.slice(start, end),
    start,
    end,
    hasOlder: start > 0,
    hasNewer: end < items.length,
    onScroll,
    goToLatest,
    goToId,
  };
}
