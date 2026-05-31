import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "cerno-theme";

function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "dark" || v === "light" ? v : null;
  } catch {
    return null;
  }
}

/**
 * Theme state for a surface. The stored preference is shared across the whole
 * app and is only written on an explicit toggle, so each surface keeps its own
 * default until the user chooses (landing defaults dark, app defaults light).
 */
export function useThemeState(defaultTheme: Theme): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme() ?? defaultTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const set = (next: Theme) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    setTheme(next);
  };

  return { theme, toggle: () => set(theme === "dark" ? "light" : "dark") };
}

export const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "light",
  toggle: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}
