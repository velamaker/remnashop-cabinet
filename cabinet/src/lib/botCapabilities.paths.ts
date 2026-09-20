/**
 * Сторож «не забыть»: каждый путь API, которого кабинет не звал в v1.3.8, должен
 * быть классифицирован — нужен ли под него новый бот.
 *
 * Модуль импортирует ТОЛЬКО тест (botCapabilities.guard.test.ts), в сборку он не
 * попадает. Граница — v1.3.8: это последний выпуск, чей бот не знает поля
 * `bot_capabilities`. Всё, что кабинет начал звать позже, со старым ботом ответит 404.
 *
 * Новый путь добавляется в ОДНУ из таблиц:
 *  - PATH_NEEDS_BOT — путь нужен функции, которую кабинет прячет по токену (вход в
 *    функцию виден до ответа бота, пишется поле или обещаются деньги/дни);
 *  - PATH_GRACEFUL — со старым ботом кабинет переживает отсутствие сам: 404
 *    превращается в «блока нет» без ошибки. Причина словами — обязательна: по ней
 *    следующий человек проверит, что это всё ещё правда.
 *
 * Честно о пределах: ловятся новые РУЧКИ. Новое ПОЛЕ в старой ручке (как тумблер
 * апселла в PUT /admin/appearance) сторож не видит — там работает правило из шапки
 * botCapabilities.ts.
 */
import type { BotCap } from "./botCapabilities";

export const PATH_NEEDS_BOT: Record<string, BotCap> = {
  // «Пользователи» → «Массово по фильтру»: опции и панель задач прячет canFeature.
  "GET /api/admin/users/bulk/days/preview": "bulk_jobs",
  "GET /api/admin/users/bulk/message/preview": "bulk_jobs",
  "GET /api/admin/users/bulk/jobs": "bulk_jobs",
  "GET /api/admin/users/bulk/jobs/{}": "bulk_jobs",
  "GET /api/admin/users/bulk/jobs/{}/items": "bulk_jobs",
  "POST /api/admin/users/bulk/days": "bulk_jobs",
  "POST /api/admin/users/bulk/message": "bulk_jobs",
  "POST /api/admin/users/bulk/message/test": "bulk_jobs",
  "POST /api/admin/users/bulk/jobs/{}/cancel": "bulk_jobs",
  "POST /api/admin/users/bulk/jobs/{}/resume": "bulk_jobs",
  // «Маркетинг» → «Скидка до окончания»: страницу прячет canPage (PAGE_NEEDS_BOT).
  "GET /api/admin/renewal-discount": "renewal_discount",
  "PUT /api/admin/renewal-discount": "renewal_discount",
  "GET /api/admin/renewal-discount/preview": "renewal_discount",
  "GET /api/admin/renewal-discount/stats": "renewal_discount",
  "POST /api/admin/renewal-discount/revoke-active": "renewal_discount",
  "POST /api/admin/renewal-discount/test-send": "renewal_discount",
  // Докупка «+1 устройства»: кнопку с суммой прячет canFeature, страницу настроек —
  // canPage, карточку пользователя — та же возможность.
  "GET /api/subscription/extra-device": "extra_device",
  "POST /api/subscription/extra-device/buy": "extra_device",
  "GET /api/admin/extra-device": "extra_device",
  "PUT /api/admin/extra-device": "extra_device",
  "GET /api/admin/subscriptions/user/{}/extra-devices": "extra_device",
  "POST /api/admin/subscriptions/user/{}/extra-devices/{}/revoke": "extra_device",
  "GET /api/subscription/extra-traffic": "extra_traffic",
  "POST /api/subscription/extra-traffic/buy": "extra_traffic",
  "GET /api/admin/extra-traffic": "extra_traffic",
  "PUT /api/admin/extra-traffic": "extra_traffic",
  "GET /api/admin/subscriptions/user/{}/extra-traffic": "extra_traffic",
  "GET /api/admin/payment-reminder": "payment_reminder",
  "PUT /api/admin/payment-reminder": "payment_reminder",
  "POST /api/admin/subscriptions/user/{}/extra-traffic/{}/revoke": "extra_traffic",
};

export const PATH_GRACEFUL: Record<string, string> = {
  "GET /api/renewal-discount":
    "useRenewalDiscount: любая ошибка = скидки нет, плашка не рисуется",
  "GET /api/admin/digest/email":
    "DigestEmailCard прячется целиком на 404/501",
  "PUT /api/admin/digest/email":
    "кнопка живёт в DigestEmailCard, который со старым ботом не рисуется",
  "GET /api/admin/digest/email/preview":
    "кнопка живёт в DigestEmailCard, который со старым ботом не рисуется",
  "GET /api/admin/digest/email/dry-run":
    "кнопка живёт в DigestEmailCard, который со старым ботом не рисуется",
  "POST /api/admin/digest/email/test":
    "кнопка живёт в DigestEmailCard, который со старым ботом не рисуется",
  "GET /api/email-optout/digest":
    "страница отписки открывается только из письма нового бота; 404 = «ссылка недействительна»",
  "POST /api/email-optout/digest":
    "страница отписки открывается только из письма нового бота; 404 = «ссылка недействительна»",
  "POST /api/email-optout/digest/resubscribe":
    "страница отписки открывается только из письма нового бота; 404 = «ссылка недействительна»",
  "POST /api/admin/users/{}/delete":
    "кнопку прячет реестр возможностей: честный 501 старого бэкенда убирает её насовсем (useCapabilities)",
  "PUT /api/admin/gateways/order":
    "стрелки порядка: отказ показывает причину и возвращает список к тому, что в базе — страница остаётся рабочей",
};

// ---------- извлечение путей из исходников ----------

/**
 * Вызовы клиентов: `api.get("/x")`, `adminApi.post<T>(\`/y/${id}\`)`. В типе-параметре
 * бывают `;` и вложенные `<>` (`<{ total: number; items: X[] }>`), поэтому он
 * пропускается целиком до `>(`, а не до первой «неподходящей» буквы.
 */
const CALL = /\b(api|adminApi)\.(get|post|put|patch|delete)\s*(?:<[^()]{0,400}?>)?\s*\(\s*(?=["'`])/g;
/** Литералы мимо клиентов: `fetch("/api/…")`, `window.location.href = "/api/…"`. */
const LITERAL = /(?=["'`]\/api\/)/g;

/**
 * Строка JS начиная с кавычки в позиции `start`: значение, где каждая вставка
 * `${…}` заменена на `{}`, и индекс сразу за закрывающей кавычкой. Вставки
 * разбираются с вложенностью — `\`/x${a ? \`?${b}\` : ""}\`` не обрывается на
 * внутренней кавычке, как обрывалась бы простая регулярка.
 */
function readString(text: string, start: number): { value: string; end: number } | null {
  const quote = text[start];
  let value = "";
  let i = start + 1;
  while (i < text.length) {
    const c = text[i]!;
    if (c === "\\") {
      value += text[i + 1] ?? "";
      i += 2;
      continue;
    }
    if (c === quote) return { value, end: i + 1 };
    if (quote === "`" && c === "$" && text[i + 1] === "{") {
      let depth = 1;
      i += 2;
      while (i < text.length && depth > 0) {
        const d = text[i]!;
        if (d === '"' || d === "'" || d === "`") {
          const inner = readString(text, i);
          if (!inner) return null;
          i = inner.end;
          continue;
        }
        if (d === "{") depth++;
        else if (d === "}") depth--;
        i++;
      }
      value += "{}";
      continue;
    }
    if (c === "\n" && quote !== "`") return null;
    value += c;
    i++;
  }
  return null;
}

/**
 * Путь без строки запроса. Хвостовая вставка без «/» перед ней — это `${query}`
 * (`/email-optout/digest${qs}`), а не параметр пути: срезаем, иначе один и тот же
 * адрес считался бы двумя.
 */
function normalize(path: string): string {
  let p = path.split("?")[0]!;
  while (p.endsWith("{}") && !p.endsWith("/{}")) p = p.slice(0, -2);
  return p;
}

/** Все пути API, которые зовут переданные исходники: «МЕТОД /api/…» или «ANY /api/…». */
export function extractApiPaths(sources: Record<string, string>): Set<string> {
  const out = new Set<string>();
  for (const text of Object.values(sources)) {
    for (const m of text.matchAll(CALL)) {
      const str = readString(text, m.index! + m[0].length);
      if (!str || !str.value.startsWith("/")) continue;
      const base = m[1] === "adminApi" ? "/api/admin" : "/api";
      out.add(`${m[2]!.toUpperCase()} ${base}${normalize(str.value)}`);
    }
    for (const m of text.matchAll(LITERAL)) {
      const str = readString(text, m.index!);
      if (!str) continue;
      // Метод у литерала из текста не узнать — и не нужно: сторожу важен сам адрес.
      out.add(`ANY ${normalize(str.value)}`);
    }
  }
  return out;
}
