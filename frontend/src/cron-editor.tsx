import { useEffect, useRef, useState } from "react";
import { Button, Input } from "./components";
import { api } from "./api";
import {
  parseCron,
  toCron,
  describeCron,
  weekdays,
  minuteIntervals,
  hourIntervals,
  type Schedule,
} from "./cron";

export function CronEditor({
  cron,
  timezone,
  onChange,
  onTimezone,
  onValidity,
}: {
  cron: string;
  timezone: string;
  onChange: (value: string) => void;
  onTimezone: (value: string) => void;
  onValidity: (valid: boolean) => void;
}) {
  const [schedule, setSchedule] = useState(() => parseCron(cron));
  const written = useRef(cron);
  const [preview, setPreview] = useState<{ values: string[]; error: string }>({
    values: [],
    error: "",
  });
  const rawInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (cron !== written.current) {
      setSchedule(parseCron(cron));
      written.current = cron;
    }
  }, [cron]);
  const expression = toCron(schedule);
  useEffect(
    () =>
      onValidity(
        expression !== null &&
          (schedule.mode === "manual" ||
            (!!expression?.trim() && !preview.error)),
      ),
    [expression, schedule.mode, preview.error, onValidity],
  );
  function update(next: Schedule) {
    setSchedule(next);
    const raw = toCron(next);
    if (raw !== null) {
      written.current = raw;
      onChange(raw);
    }
  }
  useEffect(() => {
    let cancelled = false;
    setPreview({ values: [], error: "" });
    if (!expression) return;
    const timer = setTimeout(
      () =>
        api("/routines/preview", "POST", { cron: expression, timezone })
          .then((r) => {
            if (!cancelled) setPreview({ values: r.occurrences, error: "" });
          })
          .catch((e) => {
            if (!cancelled) setPreview({ values: [], error: e.message });
          }),
      250,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [expression, timezone]);
  useEffect(() => {
    rawInput.current?.setCustomValidity(preview.error);
  }, [preview.error]);
  const choices = {
    manual: "Manual only",
    minutes: "Every few minutes",
    hourly: "Hourly",
    daily: "Daily",
    weekly: "Weekly",
    monthly: "Monthly",
    custom: "Advanced cron",
  };
  const timeMode = ["daily", "weekly", "monthly"].includes(schedule.mode);
  const zones = [
    ...new Set([
      "UTC",
      Intl.DateTimeFormat().resolvedOptions().timeZone,
      "America/Sao_Paulo",
      "America/New_York",
      "America/Los_Angeles",
      "Europe/London",
      "Europe/Lisbon",
      "Europe/Paris",
      "Asia/Tokyo",
    ]),
  ];
  return (
    <fieldset className="cron-editor">
      <legend>Schedule</legend>
      <div className="form-columns">
        <label>
          Repeat
          <select
            value={schedule.mode}
            onChange={(e) => {
              const mode = e.target.value as Schedule["mode"];
              update({
                ...parseCron(cron),
                mode,
                interval: 1,
                raw: cron || "0 8 * * *",
              });
            }}
          >
            {Object.entries(choices).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {schedule.mode !== "manual" && (
          <label>
            Timezone
            <Input
              required
              list="schedule-timezones"
              value={timezone}
              onChange={(e) => onTimezone(e.target.value)}
            />
            <datalist id="schedule-timezones">
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
          </label>
        )}
      </div>
      {schedule.mode === "manual" ? (
        <p className="cron-help">Runs only when you or the agent start it.</p>
      ) : (
        <>
          {schedule.mode === "minutes" && (
            <label>
              Interval
              <select
                value={schedule.interval}
                onChange={(e) =>
                  update({ ...schedule, interval: +e.target.value })
                }
              >
                {minuteIntervals.map((n) => (
                  <option key={n} value={n}>
                    {n} {n === 1 ? "minute" : "minutes"}
                  </option>
                ))}
              </select>
            </label>
          )}
          {schedule.mode === "hourly" && (
            <div className="form-columns">
              <label>
                Every
                <select
                  value={schedule.interval}
                  onChange={(e) =>
                    update({ ...schedule, interval: +e.target.value })
                  }
                >
                  {hourIntervals.map((n) => (
                    <option key={n} value={n}>
                      {n} {n === 1 ? "hour" : "hours"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                At minute
                <select
                  value={schedule.minute}
                  onChange={(e) =>
                    update({ ...schedule, minute: +e.target.value })
                  }
                >
                  {Array.from({ length: 60 }, (_, n) => (
                    <option key={n} value={n}>
                      {String(n).padStart(2, "0")}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          {timeMode && (
            <div className="form-columns">
              <label>
                Time
                <Input
                  type="time"
                  required
                  value={schedule.time}
                  onChange={(e) =>
                    update({ ...schedule, time: e.target.value })
                  }
                />
              </label>
              {schedule.mode === "monthly" && (
                <label>
                  Day of month
                  <select
                    value={schedule.day}
                    onChange={(e) =>
                      update({ ...schedule, day: +e.target.value })
                    }
                  >
                    {Array.from({ length: 31 }, (_, i) => (
                      <option value={i + 1} key={i}>
                        {i + 1}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          )}
          {schedule.mode === "weekly" && (
            <div>
              <span className="cron-field-label">On these days</span>
              <div className="cron-weekdays">
                {[1, 2, 3, 4, 5, 6, 0].map((day) => (
                  <Button
                    key={day}
                    aria-pressed={schedule.days.includes(day)}
                    onClick={() =>
                      update({
                        ...schedule,
                        days: schedule.days.includes(day)
                          ? schedule.days.filter((d) => d !== day)
                          : [...schedule.days, day],
                      })
                    }
                  >
                    {weekdays[day]}
                  </Button>
                ))}
              </div>
              {!schedule.days.length && (
                <p className="cron-error">Select at least one day.</p>
              )}
            </div>
          )}
          {schedule.mode === "monthly" && schedule.day > 28 && (
            <p className="cron-help">
              Months without day {schedule.day} are skipped.
            </p>
          )}
          {schedule.mode === "custom" ? (
            <label>
              Cron expression
              <Input
                ref={rawInput}
                required
                value={schedule.raw}
                placeholder="0 8 * * 1-5"
                onChange={(e) => update({ ...schedule, raw: e.target.value })}
              />
              <small>Minute · hour · day of month · month · weekday</small>
              {parseCron(schedule.raw).mode !== "custom" &&
                schedule.raw.trim() && (
                  <Button
                    className="cron-switch"
                    onClick={() => update(parseCron(schedule.raw))}
                  >
                    Use visual editor
                  </Button>
                )}
            </label>
          ) : (
            <div className="cron-expression">
              <code>{expression || "Choose a time and days"}</code>
              <Button
                onClick={() =>
                  update({ ...schedule, mode: "custom", raw: cron })
                }
              >
                Edit cron
              </Button>
            </div>
          )}
          {preview.error ? (
            <p className="cron-error" role="alert">
              {preview.error}
            </p>
          ) : (
            expression && (
              <div className="cron-preview">
                <strong>
                  {describeCron(expression)} · {timezone}
                </strong>
                <small>
                  Next 3 scheduled times
                  {preview.values.length ? "" : " · checking…"}
                </small>
                <ol>
                  {preview.values.map((v) => (
                    <li key={v}>
                      {v.slice(0, 16).replace("T", " ")}{" "}
                      <small>UTC{v.slice(-6)}</small>
                    </li>
                  ))}
                </ol>
              </div>
            )
          )}
        </>
      )}
    </fieldset>
  );
}
