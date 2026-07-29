import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { appearanceApi, type Appearance } from "@/api/appearance";
import { lighten, darken, rgba, luminance, normalizeHex } from "@/lib/color";
import { canFeature, type FeatureKey } from "@/lib/features";
import { useTheme } from "@/contexts/ThemeContext";
import { translate } from "@/i18n/translate";
import { useI18n } from "@/i18n/I18nContext";
import { enabledLanguages, DEFAULT_LANG } from "@/i18n/config";

const DEFAULT_BRAND = "RemnaShop";

interface BrandingValue {
  brandName: string;
  /** Username поддержки (без @) из конфигурации бота; null — если не задан. */
  supportUsername: string | null;
  /** Вход через Telegram по OIDC включён на боте (иначе — классический виджет). */
  telegramOidcEnabled: boolean;
  appearance: Appearance | null;
  /** Бэкенд бота не отвечает: показываем заглушку, бренд берём из кэша. */
  offline: boolean;
  /** Логотип: из кэша, если сервер лежит. */
  logoSrc: string | null;
  /** Перечитать оформление с сервера и применить (после сохранения в админке). */
  refresh: () => Promise<void>;
  /** Умеет ли бэкенд эту возможность. Неизвестное считается доступным. */
  can: (key: FeatureKey) => boolean;
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

// Кэш оформления. Когда бэкенд бота отвалился, кабинет всё равно должен выглядеть
// своим: название сервиса и логотип берём из последнего успешного ответа. Логотип
// храним data-URL'ом — ссылка на картинку ведёт на тот же упавший сервер.
const CACHE_KEY = "cabinet-appearance";
const LOGO_KEY = "cabinet-appearance-logo";

function readCache(): Appearance | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    // Список возможностей из кэша НЕ берём: браузер мог сохранить его от другого
    // бэкенда (превью, переезд, вторая установка на том же адресе), и кабинет
    // спрятал бы рабочие разделы. Без него действует дефолт «умеет всё».
    const { features: _ignored, ...rest } = JSON.parse(raw) as Appearance;
    return rest as Appearance;
  } catch {
    return null;
  }
}

function readLogo(): string | null {
  try {
    return localStorage.getItem(LOGO_KEY);
  } catch {
    return null;
  }
}

/** Складывает логотип в кэш как data-URL (один раз на новый адрес картинки). */
async function cacheLogo(url: string | null | undefined): Promise<void> {
  if (!url) return;
  try {
    if (localStorage.getItem(LOGO_KEY + ":src") === url && readLogo()) return;
    const res = await fetch(url, { cache: "force-cache" });
    if (!res.ok) return;
    const blob = await res.blob();
    if (blob.size > 400_000) return; // в localStorage большой картинке не место
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
    localStorage.setItem(LOGO_KEY, dataUrl);
    localStorage.setItem(LOGO_KEY + ":src", url);
  } catch {
    /* кэш логотипа — не критично */
  }
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  // Стартуем сразу с кэша: пока идёт запрос (или если он упадёт) бренд уже на месте.
  const [appearance, setAppearance] = useState<Appearance | null>(() => readCache());
  const [offline, setOffline] = useState(false);
  const failures = useRef(0);
  const { resolved } = useTheme();  // "dark" | "light"
  const { lang, setLang } = useI18n();

  const refresh = useCallback(async () => {
    try {
      const a = await appearanceApi.get();
      failures.current = 0;
      setOffline(false);
      setAppearance(a);
      applyAccent(a.accent);
      if (a.brand_name) document.title = `${a.brand_name} — ${translate("common.personalCabinet")}`;
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify(a));
      } catch {
        /* приватный режим браузера — переживём */
      }
      void cacheLogo(a.logo_url);
    } catch {
      // Бэкенд бота не ответил. Оформление берём из кэша, а после второй неудачи
      // подряд показываем заглушку «идут технические работы» (одиночный сбой сети
      // не должен выкидывать пользователя из кабинета).
      const cached = readCache();
      if (cached) {
        setAppearance((prev) => prev ?? cached);
        applyAccent(cached.accent);
      }
      failures.current += 1;
      if (failures.current >= 2) setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Пока бэкенд молчит — тихо пробуем снова; как ответит, заглушка уйдёт сама.
  useEffect(() => {
    if (!offline) return;
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [offline, refresh]);

  // Если активный язык отключён в оформлении — возвращаемся к русскому.
  useEffect(() => {
    if (!appearance) return;
    const allowed = enabledLanguages(appearance.enabled_languages);
    if (!allowed.some((l) => l.code === lang)) setLang(DEFAULT_LANG);
  }, [appearance, lang, setLang]);

  // Фон применяем ПО АКТИВНОЙ ТЕМЕ: свой для тёмной/светлой (с фолбэком на
  // общий legacy-фон). Реагирует на смену темы и на обновление оформления.
  useEffect(() => {
    if (!appearance) return;
    const themed = resolved === "dark"
      ? appearance.background_dark
      : appearance.background_light;
    applyBackground(themed ?? appearance.background);
  }, [appearance, resolved]);

  const value = useMemo(
    () => ({
      brandName: appearance?.brand_name || DEFAULT_BRAND,
      supportUsername: appearance?.support_username ?? null,
      telegramOidcEnabled: appearance?.telegram_oidc_enabled ?? false,
      appearance,
      refresh,
      offline,
      // Когда сервер недоступен, ссылка на логотип не загрузится — отдаём копию.
      logoSrc: (offline ? readLogo() : null) ?? appearance?.logo_url ?? null,
      can: (key: FeatureKey) => canFeature(appearance, key),
    }),
    [appearance, refresh, offline],
  );

  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}

export function useBranding() {
  const ctx = useContext(BrandingContext);
  if (!ctx) throw new Error("useBranding must be used within BrandingProvider");
  return ctx;
}
