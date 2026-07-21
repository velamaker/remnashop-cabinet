// Языки кабинета (СНГ кроме Украины + английский + турецкий). Владелец 4 июля 2026.
export type Lang =
  | "ru" | "en" | "es" | "tr" | "kk" | "ky" | "uz" | "tg" | "hy" | "az" | "be" | "ro";

export interface LangMeta {
  code: Lang;
  label: string; // самоназвание
  /** ISO-код страны для картинки-флага (flagcdn) — emoji-флаги не рендерятся на Windows. */
  country: string;
  rtl?: boolean;
}

export const LANGUAGES: LangMeta[] = [
  { code: "ru", label: "Русский", country: "ru" },
  { code: "en", label: "English", country: "gb" },
  { code: "es", label: "Español", country: "cu" },
  { code: "tr", label: "Türkçe", country: "tr" },
  { code: "kk", label: "Қазақша", country: "kz" },
  { code: "ky", label: "Кыргызча", country: "kg" },
  { code: "uz", label: "Oʻzbekcha", country: "uz" },
  { code: "tg", label: "Тоҷикӣ", country: "tj" },
  { code: "hy", label: "Հայերեն", country: "am" },
  { code: "az", label: "Azərbaycan", country: "az" },
  { code: "be", label: "Беларуская", country: "by" },
  { code: "ro", label: "Română", country: "md" },
];

export const DEFAULT_LANG: Lang = "ru";
export const STORAGE_KEY = "cabinet-lang";

/** BCP-47 локаль для Intl (форматирование дат/чисел) по языку кабинета. */
export const LOCALES: Record<Lang, string> = {
  ru: "ru-RU",
  en: "en-US",
  es: "es-ES",
  tr: "tr-TR",
  kk: "kk-KZ",
  ky: "ky-KG",
  uz: "uz-UZ",
  tg: "tg-TJ",
  hy: "hy-AM",
  az: "az-AZ",
  be: "be-BY",
  ro: "ro-RO",
};

export function isLang(v: string | null | undefined): v is Lang {
  return !!v && LANGUAGES.some((l) => l.code === v);
}

/** Языки, доступные по настройке оформления (null/пусто = все). ru всегда включён. */
export function enabledLanguages(codes: string[] | null | undefined): LangMeta[] {
  if (!codes || codes.length === 0) return LANGUAGES;
  const set = new Set(codes.map((c) => c.toLowerCase()));
  set.add(DEFAULT_LANG); // русский — язык-исходник, отключить нельзя
  const list = LANGUAGES.filter((l) => set.has(l.code));
  return list.length ? list : LANGUAGES;
}

/** Язык из устройства: перебираем ВСЕ предпочитаемые локали (navigator.languages,
 * затем navigator.language) и берём первую, которую поддерживаем. Региональный код
 * («ru-RU», «en-GB») отсекаем до 2 букв. */
export function detectDeviceLang(): Lang | null {
  const prefs = [
    ...(Array.isArray(navigator.languages) ? navigator.languages : []),
    navigator.language,
  ].filter(Boolean);
  for (const loc of prefs) {
    const two = loc.slice(0, 2).toLowerCase();
    if (isLang(two)) return two;
  }
  return null;
}

/** Начальный язык: сохранённый выбор → язык устройства → дефолт (ru). */
export function detectInitialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
  } catch {
    /* localStorage может быть недоступен */
  }
  return detectDeviceLang() ?? DEFAULT_LANG;
}
