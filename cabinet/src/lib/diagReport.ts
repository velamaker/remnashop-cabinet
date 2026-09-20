import type { DeviceResponse } from "@/types/api";
import { appFromUserAgent } from "./deviceGroups";

/**
 * «Паспорт обращения»: что спросить у человека перед тикетом и как сложить ответ.
 *
 * ЗАЧЕМ. Тикет из самодиагностики приходил владельцу с результатами проверок и
 * строкой «Опишите проблему подробнее:» — то есть без главного: на каком аппарате
 * не работает и что именно не открывается. Дальше шла переписка на два-три круга,
 * а половина людей на уточняющий вопрос не отвечает вовсе. Спросить это в самом
 * мастере — два касания, и обращение приходит сразу полным.
 *
 * ЧТО СПРАШИВАЕМ. Аппарат берём из СВОИХ данных: список устройств подписки уже
 * загружен для проверки лимита, и в нём есть модель, версия ОС и приложение
 * (HWID выдаёт каждое приложение отдельно — см. deviceGroups). Человеку остаётся
 * ткнуть в свой аппарат, а не описывать его словами. Если устройств в панели нет
 * (ни разу не подключался — частый случай), показываем список платформ.
 *
 * ПРО ЯЗЫК. Тикет уходит на языке человека, а читает его владелец. Поэтому в конце
 * тела — короткая тех-строка латиницей со стабильными ключами: её видно одинаково
 * и в турецком, и в армянском обращении.
 */

/** Что именно не работает. Ключи стабильны: уходят в тех-строку тикета. */
export const PROBLEM_KEYS = [
  "noconnect",
  "sites",
  "slow",
  "instagram",
  "youtube",
  "tiktok",
  "whatsapp",
  "telegram",
  "chatgpt",
  "games",
  "other",
] as const;
export type ProblemKey = (typeof PROBLEM_KEYS)[number];

/** Платформы для случая, когда своих устройств в панели ещё нет. */
export const PLATFORM_KEYS = ["ios", "android", "windows", "macos", "tv", "router", "other"] as const;
export type PlatformKey = (typeof PLATFORM_KEYS)[number];

/**
 * Починить «кракозябры» в имени устройства.
 *
 * Панель отдаёт модель так, как её прислало приложение, и русские имена
 * компьютеров приезжают UTF-8, прочитанным как latin-1: «ноут_x86_64» →
 * «Ð½Ð¾ÑƒÑ‚_x86_64». Человек не должен выбирать аппарат по такой подписи, а
 * владелец — разбирать её в тикете. Чиним только явные случаи: строка целиком в
 * пределах latin-1 и содержит характерные «Ð/Ñ/Ã».
 */
/**
 * Байты, которые Windows-1252 показывает не как latin-1 (диапазон 0x80–0x9F).
 * Без этой таблицы «ноут» («Ð½Ð¾ÑƒÑ‚») не чинится: там есть «ƒ» и «‚».
 */
const CP1252: Record<string, number> = {
  "€": 0x80, "‚": 0x82, "ƒ": 0x83, "„": 0x84, "…": 0x85, "†": 0x86, "‡": 0x87,
  "ˆ": 0x88, "‰": 0x89, "Š": 0x8a, "‹": 0x8b, "Œ": 0x8c, "Ž": 0x8e, "‘": 0x91,
  "’": 0x92, "“": 0x93, "”": 0x94, "•": 0x95, "–": 0x96, "—": 0x97,
  "˜": 0x98, "™": 0x99, "š": 0x9a, "›": 0x9b, "œ": 0x9c, "ž": 0x9e, "Ÿ": 0x9f,
};

export function fixMojibake(text: string): string {
  if (!/[ÐÑÃ]/.test(text)) return text;
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    const byte = code < 0x100 ? code : CP1252[text[i]!];
    if (byte === undefined) return text; // не похоже на кракозябры — не трогаем
    bytes[i] = byte;
  }
  try {
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    // Пустой результат или снова нечитаемое — оставляем как было.
    return decoded.trim() ? decoded : text;
  } catch {
    return text;
  }
}

/** Короткая подпись для кнопки: «iPhone 14 · v2RayTun» (без версии ОС). */
export function deviceChipLabel(d: DeviceResponse, unknown: string, max = 34): string {
  const label = deviceChoiceLabel({ ...d, os_version: null }, unknown);
  return label.length > max ? label.slice(0, max - 1).trimEnd() + "…" : label;
}

/** Подпись аппарата для тикета: «iPhone 14 · v2RayTun · iOS 17.4». */
export function deviceChoiceLabel(d: DeviceResponse, unknown: string): string {
  const app = appFromUserAgent(d.user_agent);
  const model = d.device_model ? fixMojibake(d.device_model) : null;
  const parts = [model || d.platform || unknown, app, d.os_version].filter(
    (p): p is string => !!p && p.trim().length > 0,
  );
  // Модель и платформа часто совпадают («Android» + «Android»): дубль не печатаем.
  const seen = new Set<string>();
  return parts.filter((p) => !seen.has(p.toLowerCase()) && seen.add(p.toLowerCase())).join(" · ");
}

/**
 * Платформа по user-agent браузера (и подсказке Telegram, если кабинет открыт
 * внутри мини-аппы). Нужна, чтобы подсветить вероятный ответ, а не заставлять
 * человека искать свой телефон в списке.
 */
export function guessPlatform(ua: string | null | undefined, tgPlatform?: string | null): PlatformKey {
  const tg = (tgPlatform ?? "").toLowerCase();
  if (tg === "ios") return "ios";
  if (tg === "android") return "android";

  const s = (ua ?? "").toLowerCase();
  if (!s) return "other";
  if (/\biphone|ipad|ipod\b/.test(s)) return "ios";
  if (/android\s*tv|smarttv|googletv|appletv|tizen|web0s|webos/.test(s)) return "tv";
  if (/android/.test(s)) return "android";
  if (/windows/.test(s)) return "windows";
  if (/mac os x|macintosh/.test(s)) return "macos";
  return "other";
}

/** Браузер одним словом — для тех-строки. */
export function browserName(ua: string | null | undefined): string {
  const s = ua ?? "";
  if (/Edg\//i.test(s)) return "Edge";
  if (/OPR\/|Opera/i.test(s)) return "Opera";
  if (/YaBrowser/i.test(s)) return "Yandex";
  if (/Firefox\//i.test(s)) return "Firefox";
  if (/Chrome\//i.test(s)) return "Chrome";
  if (/Safari\//i.test(s)) return "Safari";
  return "?";
}

export interface TechFacts {
  /** Платформа выбранного аппарата или браузера. */
  platform: string;
  /** Приложение из user-agent устройства, если человек выбрал своё устройство. */
  app?: string | null;
  /** Ключи выбранных проблем. */
  problems: readonly string[];
  /** Кабинет открыт внутри мини-аппы Telegram. */
  miniApp?: boolean;
  /** User-agent браузера, из которого человек пишет. */
  ua?: string | null;
}

/**
 * Одна строка латиницей в конце тикета: владелец читает её, не разбирая язык
 * обращения. Пустые части опускаем, чтобы строка не превращалась в мусор.
 */
export function techLine(f: TechFacts): string {
  const parts = [`platform=${f.platform || "?"}`];
  if (f.app) parts.push(`app=${f.app}`);
  if (f.problems.length) parts.push(`problems=${f.problems.join(",")}`);
  parts.push(`browser=${browserName(f.ua)}`);
  if (f.miniApp) parts.push("mini-app");
  return parts.join(" · ");
}

export interface TicketReport {
  /** Подписи разделов на языке человека. */
  labels: { device: string; problems: string; comment: string; checks: string };
  device: string;
  problems: string[];
  comment?: string | null;
  /** Итог автопроверок («✅ …\n❌ …»). */
  summary: string;
  tech: string;
}

/** Тело тикета: сначала ответы человека, потом машинная часть. */
export function buildTicketBody(r: TicketReport): string {
  const blocks: string[] = [];
  const head = [`${r.labels.device}: ${r.device}`];
  if (r.problems.length) head.push(`${r.labels.problems}: ${r.problems.join(", ")}`);
  const comment = (r.comment ?? "").trim();
  if (comment) head.push(`${r.labels.comment}: ${comment}`);
  blocks.push(head.join("\n"));
  if (r.summary.trim()) blocks.push(`${r.labels.checks}:\n${r.summary.trim()}`);
  blocks.push(r.tech);
  return blocks.join("\n\n");
}

/**
 * Тема тикета: «VPN не работает — автодиагностика: Instagram, YouTube».
 *
 * Аппарат в тему не выносим: подпись бывает длинной («iPhone 14 · v2RayTun ·
 * iOS 17.4») и в списке обращений вытесняет главное — что именно не работает.
 * Ограничение ручки — 200 символов, поэтому длинный список режем.
 */
export function buildTicketSubject(base: string, problems: string[]): string {
  const what = problems.join(", ");
  const subject = what ? `${base}: ${what}` : base;
  return subject.length > 200 ? subject.slice(0, 197).trimEnd() + "…" : subject;
}
