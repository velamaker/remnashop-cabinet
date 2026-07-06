import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useTelegramTheme } from "@/hooks/useTelegramWebApp";

export type ThemeMode = "dark" | "light" | "system";
type ResolvedTheme = "dark" | "light";

const STORAGE_KEY = "cabinet-theme";

function getStoredMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "dark" || stored === "light" || stored === "system") {
    return stored;
  }
  return "system";
}

function resolveSystemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove("theme-dark", "theme-light");
  root.classList.add(`theme-${resolved}`);
  root.style.colorScheme = resolved;
}

interface ThemeContextValue {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  isMiniApp: boolean;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(getStoredMode);
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    mode === "system" ? resolveSystemTheme() : mode,
  );
  const tgTheme = useTelegramTheme();

  const setMode = useCallback((next: ThemeMode) => {
    // Ручной выбор работает и в Mini App: явный dark/light перекрывает тему
    // Telegram, «system» = следовать Telegram (Mini App) / устройству (веб).
    setModeState(next);
    localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.dataset.themeMode = next;
  }, []);

  // Резолвим тему: явные dark/light — как выбрано; «system» = тема Telegram
  // (в Mini App) либо системная тема устройства (обычный веб).
  useEffect(() => {
    const next = mode === "system" ? (tgTheme ?? resolveSystemTheme()) : mode;
    setResolved(next);
    applyTheme(next);
  }, [mode, tgTheme]);

  // Если выбран "system" — слушаем изменение темы устройства на лету.
  useEffect(() => {
    if (mode !== "system" || tgTheme) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const next = resolveSystemTheme();
      setResolved(next);
      applyTheme(next);
    };
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, [mode, tgTheme]);

  const value = useMemo(
    () => ({ mode, resolved, isMiniApp: Boolean(tgTheme), setMode }),
    [mode, resolved, tgTheme, setMode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
