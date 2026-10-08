export type ThemeMode = "dark" | "light" | "system";
export const themeKey = "fluxyr-theme";

export function parseTheme(value: string | null): ThemeMode {
  return value === "light" || value === "dark" ? value : "system";
}

export function readTheme(): ThemeMode {
  try {
    return parseTheme(localStorage.getItem(themeKey));
  } catch {
    return "system";
  }
}

export function applyTheme(mode: ThemeMode, systemDark: boolean) {
  const resolved = mode === "system" ? (systemDark ? "dark" : "light") : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.style.colorScheme = resolved;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", resolved === "dark" ? "#090c14" : "#f6f7fb");
}
