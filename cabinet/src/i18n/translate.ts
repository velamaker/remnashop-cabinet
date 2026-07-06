// Перевод вне React-контекста (для чистых функций: lib/format, api/client и т.п.).
// Активный язык хранится модульно и синхронно обновляется провайдером при смене
// языка — так format/client всегда берут актуальный перевод на том же рендере.
import { DEFAULT_LANG, detectInitialLang, type Lang } from "./config";
import { DICTIONARIES } from "./dictionaries";

let activeLang: Lang = detectInitialLang();

export function setActiveLang(l: Lang): void {
  activeLang = l;
}

export function getActiveLang(): Lang {
  return activeLang;
}

export function interpolate(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

/** Перевод по ключу. Порядок: язык → ru → сам ключ. Поддержка {vars}. */
export function translate(
  key: string,
  vars?: Record<string, string | number>,
  lang?: Lang,
): string {
  const l = lang ?? activeLang;
  const cur = DICTIONARIES[l]?.[key];
  const fallback = DICTIONARIES[DEFAULT_LANG]?.[key];
  return interpolate(cur ?? fallback ?? key, vars);
}
