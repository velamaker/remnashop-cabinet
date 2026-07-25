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
