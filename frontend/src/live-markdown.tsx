import { memo, useCallback, useSyncExternalStore } from "react";
import { LiveTextFeed, mergeLiveText } from "./live-text-data";
import { Markdown } from "./markdown";

// Only this active text subscribes to wire fragments. Chat history and the
// execution graph remain on durable events, not on the provider token cadence.
export const LiveMarkdown = memo(function LiveMarkdown({
  feed,
  block,
  text,
  end,
  live,
}: {
  feed: LiveTextFeed;
  block: string;
  text: string;
  end?: number;
  live: boolean;
}) {
  const subscribe = useCallback(
    (fn: () => void) => (live ? feed.subscribe(block, fn) : () => {}),
    [feed, block, live],
  );
  const snapshot = useCallback(
    () => (live ? feed.get(block) : undefined),
    [feed, block, live],
  );
  const tail = useSyncExternalStore(subscribe, snapshot, snapshot);
  return (
    <Markdown>
      {live ? mergeLiveText(text, end ?? text.length, tail) : text}
    </Markdown>
  );
});
