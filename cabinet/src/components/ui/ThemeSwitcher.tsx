import { clsx } from "clsx";
import { Moon, Sun, Monitor } from "lucide-react";
import { useTheme, type ThemeMode } from "@/contexts/ThemeContext";
import { useT } from "@/i18n/I18nContext";

const options: { mode: ThemeMode; icon: typeof Moon; label: string }[] = [
  { mode: "light", icon: Sun, label: "theme.light" },
  { mode: "system", icon: Monitor, label: "theme.system" },
  { mode: "dark", icon: Moon, label: "theme.dark" },
];

export function ThemeSwitcher({ vertical = false }: { vertical?: boolean }) {
  const t = useT();
  const { mode, setMode } = useTheme();

  return (
    <div
      className={clsx(
        "inline-flex items-center gap-0.5 rounded-xl border border-border bg-bg-subtle p-1",
        vertical && "flex-col",
      )}
    >
      {options.map(({ mode: optMode, icon: Icon, label }) => (
        <button
          key={optMode}
          type="button"
          title={t(label)}
          aria-label={t(label)}
          onClick={() => setMode(optMode)}
          className={clsx(
            "flex h-8 w-8 items-center justify-center rounded-lg transition-all duration-150",
            mode === optMode
              ? "bg-bg-raised text-accent shadow-soft"
              : "text-fg-subtle hover:text-fg-muted",
          )}
        >
          <Icon className="h-4 w-4" strokeWidth={2} />
        </button>
      ))}
    </div>
  );
}
