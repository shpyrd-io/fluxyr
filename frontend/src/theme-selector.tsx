import { useEffect, useState } from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
} from "./ui/select/select";
import {
  applyTheme,
  parseTheme,
  readTheme,
  themeKey,
  type ThemeMode,
} from "./theme";

const modes = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const;

export function ThemeSelector() {
  const [mode, setMode] = useState<ThemeMode>(readTheme);
  const selected = modes.find((item) => item.value === mode)!;
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => applyTheme(mode, media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [mode]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === themeKey || event.key === null) setMode(readTheme());
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  function change(value: string) {
    const next = parseTheme(value);
    applyTheme(next, window.matchMedia("(prefers-color-scheme: dark)").matches);
    setMode(next);
    try {
      localStorage.setItem(themeKey, next);
    } catch {
      // The current tab still works when browser storage is disabled.
    }
  }

  return (
    <Select value={mode} onValueChange={change}>
      <SelectTrigger
        className="theme-trigger"
        aria-label={`Theme: ${selected.label}`}
        title={`Theme: ${selected.label}`}
      >
        <selected.Icon size={17} aria-hidden="true" />
        <span className="sr-only">{selected.label}</span>
      </SelectTrigger>
      <SelectContent className="theme-menu" align="end">
        {modes.map(({ value, label, Icon }) => (
          <SelectItem key={value} value={value}>
            <span className="theme-option">
              <Icon size={15} aria-hidden="true" />
              {label}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
