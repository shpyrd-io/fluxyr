export type Schedule = {
  mode:
    "manual" | "minutes" | "hourly" | "daily" | "weekly" | "monthly" | "custom";
  interval: number;
  time: string;
  minute: number;
  days: number[];
  day: number;
  raw: string;
};
export const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const minuteIntervals = [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30];
export const hourIntervals = [1, 2, 3, 4, 6, 8, 12];
const number = (s: string, max: number, min = 0) =>
  /^\d+$/.test(s) && +s >= min && +s <= max;
export function parseCron(raw = ""): Schedule {
  const base: Schedule = {
    mode: "custom",
    interval: 1,
    time: "08:00",
    minute: 0,
    days: [1, 2, 3, 4, 5],
    day: 1,
    raw,
  };
  if (!raw.trim()) return { ...base, mode: "manual" };
  const fields = raw.trim().split(/\s+/);
  if (fields.length !== 5) return base;
  const [m, h, d, month, w] = fields;
  if (month !== "*") return base;
  if (d === "*" && w === "*") {
    const step = m === "*" ? 1 : /^\*\/\d+$/.test(m) ? +m.slice(2) : 0;
    if (h === "*" && minuteIntervals.includes(step))
      return { ...base, mode: "minutes", interval: step };
    const hours = h === "*" ? 1 : /^\*\/\d+$/.test(h) ? +h.slice(2) : 0;
    if (number(m, 59) && hourIntervals.includes(hours))
      return { ...base, mode: "hourly", minute: +m, interval: hours };
  }
  if (!number(m, 59) || !number(h, 23)) return base;
  const time = `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
  if (d === "*" && w === "*") return { ...base, mode: "daily", time };
  if (w === "*" && number(d, 31, 1))
    return { ...base, mode: "monthly", time, day: +d };
  if (d !== "*") return base; // Restricting both fields has OR semantics.
  const days = new Set<number>();
  for (const part of w.toUpperCase().split(",")) {
    const names = weekdays.map((n) => n.toUpperCase());
    const normalized = part.replace(/SUN|MON|TUE|WED|THU|FRI|SAT/g, (n) =>
      String(names.indexOf(n)),
    );
    const range = normalized.split("-");
    if (range.length > 2 || !range.every((v) => number(v, 7))) return base;
    const from = +range[0],
      to = +(range[1] ?? range[0]);
    if (to < from) return base;
    for (let i = from; i <= to; i++) days.add(i % 7);
  }
  return days.size
    ? { ...base, mode: "weekly", time, days: [...days].sort() }
    : base;
}
export function toCron(s: Schedule): string | null {
  if (s.mode === "custom") return s.raw;
  if (s.mode === "manual") return "";
  if (s.mode === "minutes")
    return `${s.interval === 1 ? "*" : `*/${s.interval}`} * * * *`;
  if (s.mode === "hourly")
    return `${s.minute} ${s.interval === 1 ? "*" : `*/${s.interval}`} * * *`;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(s.time)) return null;
  const [h, m] = s.time.split(":").map(Number);
  if (s.mode === "daily") return `${m} ${h} * * *`;
  if (s.mode === "monthly") return `${m} ${h} ${s.day} * *`;
  return s.days.length ? `${m} ${h} * * ${[...s.days].sort().join(",")}` : null;
}
export function describeCron(raw?: string): string {
  const s = parseCron(raw || "");
  switch (s.mode) {
    case "manual":
      return "Manual only";
    case "minutes":
      return s.interval === 1 ? "Every minute" : `Every ${s.interval} minutes`;
    case "hourly":
      return `${s.interval === 1 ? "Every hour" : `Every ${s.interval} hours`} at :${String(s.minute).padStart(2, "0")}`;
    case "daily":
      return `Daily at ${s.time}`;
    case "weekly":
      return `${s.days.join() === "1,2,3,4,5" ? "Weekdays" : s.days.map((d) => weekdays[d]).join(", ")} at ${s.time}`;
    case "monthly":
      return `Day ${s.day} of each month at ${s.time}`;
    default:
      return "Custom schedule";
  }
}
