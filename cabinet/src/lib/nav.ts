/**
 * Безопасный внутренний путь для редиректа после входа (`?next=…`).
 *
 * Защита от open-redirect, включая обход из CVE react-router (GHSA-wrjc-x8rr-h8h6):
 * браузер/роутер нормализует обратный слэш `\` в `/`, поэтому `/\evil.com` стал бы
 * `//evil.com` (protocol-relative) → редирект на чужой сайт. Поэтому режем ЛЮБЫЕ
 * бэкслэши, двойной ведущий слэш и всё, что не начинается с одного `/`.
 */
export function safeInternalPath(raw: string | null | undefined): string {
  if (!raw) return "/";
  if (raw.includes("\\")) return "/"; // бэкслэш → возможен обход в `//`
  if (!raw.startsWith("/")) return "/"; // только абсолютный внутренний путь
  if (raw.startsWith("//")) return "/"; // protocol-relative → внешний хост
  return raw;
}

// Разрешённые схемы для ВНЕШНИХ ссылок: http(s)/tg/mailto + известные схемы импорта
// VPN-приложений. Всё остальное (javascript:/data:/blob:/vbscript: …) отвергаем.
const _SAFE_URL_SCHEME =
  /^(https?|tg|mailto|happ|hiddify|v2raytun|v2rayng|v2box|nekobox|nekoray|streisand|sub|karing|sing-box|clash|clashmeta|shadowrocket|foxray|sfi|sfa|fair):/i;

/**
 * Безопасный ВНЕШНИЙ url для href/навигации. Непроверенный url из бэкенда/админки
 * (cta_url, install_url, deep_link, ссылки лент/уведомлений) НЕ должен исполнить JS в
 * origin кабинета: React не срезает `javascript:` из href. Пропускает разрешённую схему
 * или относительный внутренний путь (/…, но не //); иначе — undefined (ссылка инертна).
 */
export function safeExternalUrl(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  const v = raw.trim();
  if (v.startsWith("/") && !v.startsWith("//")) return v; // относительный внутренний
  return _SAFE_URL_SCHEME.test(v) ? v : undefined;
}
