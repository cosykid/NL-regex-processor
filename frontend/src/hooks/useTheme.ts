import { useCallback, useEffect, useState } from "react";
import type { Theme } from "../lib/api-types";

const THEME_KEY = "ds.theme";

export function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Colour theme state: applies + persists the theme and exposes a toggle. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  /* ---- apply + persist the colour theme ---- */
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b0e15" : "#f5f6f8");
  }, [theme]);

  const toggleTheme = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    []
  );

  return { theme, toggleTheme };
}
