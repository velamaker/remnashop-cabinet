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

export function formatTrafficLimit(limitBytes: number): string {
  if (limitBytes === 0) return translate("fmt.unlimited");
  return formatBytes(limitBytes);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(locale(), {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function daysUntil(iso: string): number {
  const diff = new Date(iso).getTime() - Date.now();
  return Math.ceil(diff / (1000 * 60 * 60 * 24));
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
