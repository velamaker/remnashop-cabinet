// Языки кабинета (СНГ кроме Украины + английский + турецкий). Владелец 4 июля 2026.
export type Lang =
  | "ru" | "en" | "tr" | "kk" | "ky" | "uz" | "tg" | "hy" | "az" | "be" | "ro";

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

/** Начальный язык: сохранённый → язык браузера → дефолт (ru). */
export function detectInitialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
  } catch {
    /* localStorage может быть недоступен */
  }
  const nav = (navigator.language || "").slice(0, 2).toLowerCase();
  if (isLang(nav)) return nav;
  return DEFAULT_LANG;
}
