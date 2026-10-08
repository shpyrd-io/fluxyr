// One request at a time, coalesced notifications and a slow fallback poll.
export function createPoller(
  run: () => Promise<unknown>,
  interval = 30_000,
  minimum = 2_000,
  visible = () => typeof document === "undefined" || !document.hidden,
) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<unknown> | undefined;
  let next = 0;
  let dirty = false;

  function schedule(delay: number) {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(() => void refresh(), delay);
  }
  function refresh(): Promise<unknown> {
    if (stopped) return Promise.resolve();
    if (!visible()) {
      schedule(interval);
      return Promise.resolve();
    }
    if (inFlight) {
      dirty = true;
      return inFlight;
    }
    if (Date.now() < next) {
      schedule(next - Date.now());
      return Promise.resolve();
    }
    clearTimeout(timer);
    dirty = false;
    next = Date.now() + minimum;
    inFlight = Promise.resolve()
      .then(run)
      .finally(() => {
        inFlight = undefined;
        schedule(dirty ? Math.max(0, next - Date.now()) : interval);
      });
    return inFlight;
  }
  return {
    refresh,
    start() {
      stopped = false;
      void refresh();
    },
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
  };
}
