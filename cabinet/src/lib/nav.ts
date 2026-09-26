// Управляющие символы, пробелы и невидимые разделители. Главное здесь — \t \n \r:
// парсер URL выкидывает их из ЛЮБОГО места адреса молча, поэтому `/\t/evil.com`
// проходит проверку «один ведущий слэш», а браузер читает его как `//evil.com`.
// react-router 6.30 на таком адресе ловит SecurityError в pushState и уходит через
// window.location.assign — то есть на чужой домен. Остальные пробельные и
// невидимые символы в настоящем внутреннем пути не встречаются вовсе (в адресе
// они были бы закодированы), зато годятся для подмены — их тоже не пускаем.
// Правило повторено на сервере (auth_oidc.safe_next_path) — меняй оба места.
const _UNSAFE_PATH_CHARS =
  // eslint-disable-next-line no-control-regex -- управляющие символы здесь и есть предмет проверки
  /[\u0000-\u0020\u007f-\u00a0\u1680\u2000-\u200f\u2028-\u202f\u205f-\u206f\u3000\ufeff]/;

// Два разделителя в начале, в том числе закодированные: `//`, `/\`, `/%2F`, `/%5C`.
// Сам браузер `%2F` в пути не раскодирует, но любой слой, который раскодирует адрес
// ещё раз (прокси, сервер, чужой роутер), получит `//evil` — адрес чужого хоста.
const _DOUBLE_LEAD = /^[/\\](?:[/\\]|%2f|%5c)/i;

// Сегмент «.» или «..», в том числе в виде %2e. Парсер URL схлопывает их, и
// `/.//evil.com` превращается в путь `//evil.com`: на своём origin он безвреден,
// но отдай его роутеру как есть — получится адрес чужого хоста. Настоящему
// внутреннему пути такие сегменты не нужны.
// (Обычной функцией, а не регулярным выражением с группой в начале строки: тест
// connectSchemes ищет в этом файле первое такое выражение и считает его списком
// схем приложений — лишнее сбило бы его с толку. Здесь нельзя писать и пример.)
function isDotSegment(seg: string): boolean {
  const s = seg.replace(/%2e/gi, ".");
  return s === "." || s === "..";
}

/**
 * Безопасный внутренний путь для перехода после входа (`?next=…`).
 *
 * Защита от open-redirect. Проверяем два раза: сначала сам текст (управляющие
 * символы, бэкслэш — из CVE react-router GHSA-wrjc-x8rr-h8h6, двойной и
 * закодированный ведущий слэш, dot-сегменты), затем разбираем адрес тем же
 * парсером, что и браузер, от своего origin и сверяем origin. Отдаём уже
 * НОРМАЛИЗОВАННЫЙ путь (pathname+search+hash) — ровно то, что увидит браузер, а
 * не исходную строку, которую он мог бы прочитать иначе. Непригодное → «/».
 */
export function safeInternalPath(raw: string | null | undefined): string {
  if (!raw) return "/";
  if (_UNSAFE_PATH_CHARS.test(raw)) return "/";
  if (raw.includes("\\")) return "/"; // бэкслэш → возможен обход в `//`
  if (!raw.startsWith("/")) return "/"; // только абсолютный внутренний путь
  if (_DOUBLE_LEAD.test(raw)) return "/"; // protocol-relative → внешний хост
  const pathname = raw.split(/[?#]/, 1)[0] ?? "";
  if (pathname.split("/").some(isDotSegment)) return "/";

  const origin = window.location.origin;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return "/"; // origin «null» (file://, песочница) или битый адрес
  }
  if (url.origin !== origin) return "/";
  const normalized = url.pathname + url.search + url.hash;
  // Итог обязан сам проходить те же правила: отдаём его роутеру и в ?next= дальше.
  if (!normalized.startsWith("/") || _DOUBLE_LEAD.test(normalized)) return "/";
  return normalized;
}

/**
 * Адрес страницы с сохранённым `?next=`. Вход, регистрация и кнопка OIDC передают
 * next друг другу: человек со ссылки-сертификата может по дороге сменить способ
 * входа («Нет аккаунта» → регистрация → «Войти»), и код подарка не должен
 * теряться ни на одном шаге. next = «/» не пишем: это и так путь по умолчанию.
 */
export function withNext(path: string, next: string | null | undefined): string {
  const target = safeInternalPath(next);
  if (target === "/") return path;
  return `${path}${path.includes("?") ? "&" : "?"}next=${encodeURIComponent(target)}`;
}

// Разрешённые схемы для ВНЕШНИХ ссылок: http(s)/tg/mailto + известные схемы импорта
// VPN-приложений. Всё остальное (javascript:/data:/blob:/vbscript: …) отвергаем.
//
// ВАЖНО: добавил приложение в data/apps.ts — добавь сюда его схему. Забудешь —
// кнопка «Подключиться» будет молча не работать: ссылка отбраковывается здесь,
// openExternalLink выходит без действия, а пользователь видит «Открываем…».
// Ровно так вышло с INCY. За этим следит тест lib/nav.test.ts, он сверяет
// список схем apps.ts с этим регулярным выражением.
const _SAFE_URL_SCHEME =
  /^(https?|tg|mailto|happ|hiddify|v2raytun|v2rayng|v2box|nekobox|nekoray|streisand|sub|karing|sing-box|clash|clashmeta|shadowrocket|foxray|sfi|sfa|fair|incy):/i;

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
