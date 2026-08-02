import { LOCALES } from "@/i18n/config";
import { getActiveLang, translate } from "@/i18n/translate";

/** Активная BCP-47 локаль для Intl (даты/числа) по выбранному языку кабинета. */
export function activeLocale(): string {
  return LOCALES[getActiveLang()] ?? "ru-RU";
}

const locale = activeLocale;

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes === 0) return `0 ${translate("fmt.gb")}`;
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(gb >= 10 ? 0 : 1)} ${translate("fmt.gb")}`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} ${translate("fmt.mb")}`;
}

/**
 * Лимит трафика приходит из API в ГИГАБАЙТАХ, а расход (`used_traffic_bytes`) — в
 * байтах. Так это устроено в самом бэкенде: `subscriptions.traffic_limit` и
 * `plans.traffic_limit` хранятся в ГБ, в байты переводятся только на выходе в
 * Remnawave. Сравнивать лимит с расходом и печатать его одной функцией можно лишь
 * после перевода в байты — иначе 5 ГБ читаются как 5 байт («0 МБ», прогресс-бар
 * всегда полный, «трафик исчерпан» у всех).
 */
export const GB = 1024 ** 3;

export function trafficLimitBytes(limitGb: number | null | undefined): number {
  return (limitGb ?? 0) * GB;
}

/** Лимит (в ГБ) человеческой строкой; 0 = безлимит. */
export function formatTrafficLimit(limitGb: number): string {
  if (limitGb === 0) return translate("fmt.unlimited");
  return formatBytes(trafficLimitBytes(limitGb));
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(locale(), {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * Сколько ПОЛНЫХ суток осталось до даты. Округление вниз — не вкусовщина: оба
 * бэкенда считают дни именно так (`(expire_at - now) // 86400` в freeze-status),
 * и при округлении вверх на одном экране оказывались рядом «Осталось 8 дн.» от
 * бэкенда (виджет паузы) и «9 дн. осталось» от кабинета — одна и та же подписка
 * с двумя разными числами.
 *
 * ВАЖНО: 0 здесь значит «меньше суток», а НЕ «истекла» — до конца срока ещё
 * есть время. Для проверки истечения есть isExpired(), который смотрит на саму
 * дату; сравнивать `daysUntil(...) <= 0` нельзя, иначе живая подписка в
 * последний день объявляется истёкшей.
 */
export function daysUntil(iso: string): number {
  const diff = new Date(iso).getTime() - Date.now();
  return Math.floor(diff / (1000 * 60 * 60 * 24));
}

/** Срок уже прошёл. Отдельно от daysUntil: «0 полных суток» ≠ «истекла». */
export function isExpired(iso: string): boolean {
  return new Date(iso).getTime() <= Date.now();
}

export function formatRelativeOnline(iso: string | null): string {
  if (!iso) return translate("fmt.never");
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return translate("fmt.onlineNow");
  if (minutes < 60) return translate("fmt.minAgo", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return translate("fmt.hoursAgo", { n: hours });
  const days = Math.floor(hours / 24);
  return translate("fmt.daysAgo", { n: days });
}
