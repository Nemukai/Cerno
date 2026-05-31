import type { ReactNode } from "react";
import { Moon, Sun } from "lucide-react";
import { ThemeContext, useTheme, useThemeState, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

export function ThemeProvider({
  defaultTheme,
  children,
}: {
  defaultTheme: Theme;
  children: ReactNode;
}) {
  const value = useThemeState(defaultTheme);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle theme"
      title={theme === "dark" ? "Switch to light" : "Switch to dark"}
      className={cn(
        "flex h-8 w-8 items-center justify-center border border-border text-foreground/60 transition-colors hover:border-primary/60 hover:text-primary",
        className,
      )}
    >
      {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
    </button>
  );
}
