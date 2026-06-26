import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { appearanceApi, type Appearance } from "@/api/appearance";
import { lighten, darken, rgba, luminance, normalizeHex } from "@/lib/color";

const DEFAULT_BRAND = "RemnaShop";

interface BrandingValue {
  brandName: string;
  /** Username поддержки (без @) из конфигурации бота; null — если не задан. */
  supportUsername: string | null;
  appearance: Appearance | null;
  /** Перечитать оформление с сервера и применить (после сохранения в админке). */
  refresh: () => Promise<void>;
}

const BrandingContext = createContext<BrandingValue | null>(null);

const ACCENT_VARS = ["--accent", "--accent-hover", "--accent-2", "--accent-subtle", "--accent-glow"];
// При кастомном фоне переопределяем не только поверхности, но и цвет текста и
// границ — иначе тёмный текст темы теряется на тёмном фоне (и наоборот).
const BG_VARS = [
  "--bg", "--bg-subtle", "--bg-raised", "--bg-overlay",
  "--fg", "--fg-muted", "--fg-subtle", "--border", "--border-subtle",
];

const LIGHT_FG = "238, 243, 251"; // #eef3fb — текст для тёмного фона
const DARK_FG = "28, 27, 23";     // #1c1b17 — текст для светлого фона

/** Применяет акцентный цвет, derive-я оттенки. null → возврат к теме. */
export function applyAccent(accent: string | null) {
  const root = document.documentElement.style;
  const hex = normalizeHex(accent);
  if (!hex) {
    ACCENT_VARS.forEach((p) => root.removeProperty(p));
    return;
  }
  root.setProperty("--accent", hex);
  root.setProperty("--accent-hover", lighten(hex, 0.16));
  root.setProperty("--accent-2", lighten(hex, 0.22));
  root.setProperty("--accent-subtle", rgba(hex, 0.12));
  root.setProperty("--accent-glow", rgba(hex, 0.3));
}

/** Применяет базовый цвет фона, derive-я поверхности по яркости. null → тема. */
export function applyBackground(background: string | null) {
  const root = document.documentElement.style;
  const hex = normalizeHex(background);
  if (!hex) {
    BG_VARS.forEach((p) => root.removeProperty(p));
    return;
  }
  const dark = luminance(hex) < 0.5;
  root.setProperty("--bg", hex);
  root.setProperty("--bg-subtle", dark ? lighten(hex, 0.04) : darken(hex, 0.03));
  root.setProperty("--bg-raised", dark ? lighten(hex, 0.08) : lighten(hex, 0.5));
  root.setProperty("--bg-overlay", dark ? lighten(hex, 0.12) : darken(hex, 0.06));

  // Подбираем текст и границы под яркость фона, чтобы буквы не терялись.
  const fg = dark ? LIGHT_FG : DARK_FG;
  root.setProperty("--fg", `rgb(${fg})`);
  root.setProperty("--fg-muted", `rgba(${fg}, 0.62)`);
  root.setProperty("--fg-subtle", `rgba(${fg}, 0.40)`);
  root.setProperty("--border", `rgba(${fg}, 0.16)`);
  root.setProperty("--border-subtle", `rgba(${fg}, 0.09)`);
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance | null>(null);

  const refresh = useCallback(async () => {
    try {
      const a = await appearanceApi.get();
      setAppearance(a);
      applyAccent(a.accent);
      applyBackground(a.background);
      if (a.brand_name) document.title = `${a.brand_name} — личный кабинет`;
    } catch {
      // Оформление не критично — при ошибке остаются цвета темы.
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ brandName: appearance?.brand_name || DEFAULT_BRAND, supportUsername: appearance?.support_username ?? null, appearance, refresh }),
    [appearance, refresh],
  );

  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}

export function useBranding() {
  const ctx = useContext(BrandingContext);
  if (!ctx) throw new Error("useBranding must be used within BrandingProvider");
  return ctx;
}
