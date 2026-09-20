/**
 * Массовые задачи «Пользователей» («+N дней», «Написать») — чистые помощники.
 *
 * Логика «кому» живёт на бэкенде (services/overlay_bulk.py); здесь только то, как
 * её цифры показать админу. Строки — из словаря по ключам adm.bulk.*: функции
 * чистые, поэтому переводит их translate, а не хук.
 */
import type { BulkJob, BulkJobStatus } from "@/api/admin";
import type { Lang } from "@/i18n/config";
import { getActiveLang, translate } from "@/i18n/translate";
import { pluralFor } from "@/lib/pluralRu";

/** Кто не получит дни — порядок и ключи подписей блока «Не получат». */
export const SKIP_LINES: { key: string; label: string }[] = [
  { key: "NO_SUBSCRIPTION", label: "adm.bulk.skip_no_subscription" },
  { key: "EXPIRED", label: "adm.bulk.skip_expired" },
  { key: "RESERVE", label: "adm.bulk.skip_reserve" },
  { key: "DISABLED", label: "adm.bulk.skip_disabled" },
  { key: "UNLIMITED", label: "adm.bulk.skip_unlimited" },
  { key: "TRIAL", label: "adm.bulk.skip_trial" },
  { key: "LIMITED", label: "adm.bulk.skip_limited" },
  { key: "BLOCKED", label: "adm.bulk.skip_blocked" },
];

/** Строки «Не получат»: нулевые категории не показываем — это шум. */
export function skippedLines(skipped: Record<string, number> | undefined): string[] {
  if (!skipped) return [];
  return SKIP_LINES.filter(({ key }) => (skipped[key] ?? 0) > 0).map(({ key, label }) =>
    translate(label, { n: skipped[key] ?? 0 }),
  );
}

/** Примерное время прогона, в минутах (не меньше одной).
 *
 *  Дни: на человека GET и PATCH панели плюс пауза 0,3 с и финальная сверка — около
 *  секунды. Сообщение: отправка в Telegram и пауза 0,05 с — около 0,2 секунды. */
export function estimateMinutes(kind: "days" | "message", people: number): number {
  const perPerson = kind === "days" ? 1 : 0.2;
  return Math.max(1, Math.ceil((people * perPerson) / 60));
}

const STATUS_KEYS: Record<BulkJobStatus, string> = {
  QUEUED: "adm.bulk.status_queued",
  PROCESSING: "adm.bulk.status_processing",
  PAUSED: "adm.bulk.status_paused",
  CANCELING: "adm.bulk.status_canceling",
  COMPLETED: "adm.bulk.status_completed",
  CANCELED: "adm.bulk.status_canceled",
  ERROR: "adm.bulk.status_error",
};

export function jobStatusLabel(status: BulkJobStatus): string {
  const key = STATUS_KEYS[status];
  return key ? translate(key) : status;
}

/** Задача ещё может что-то поменять у людей — опрашиваем её прогресс. */
export function isActive(status: BulkJobStatus): boolean {
  return status === "QUEUED" || status === "PROCESSING" || status === "CANCELING";
}

export function jobKindLabel(kind: BulkJob["kind"]): string {
  return translate(kind === "days" ? "adm.bulk.kind_days" : "adm.bulk.kind_message");
}

/** «добавлено 20 · пропущено 3 · ошибок 0» с хвостом про неизвестные и ручную проверку. */
export function jobCounters(job: BulkJob): string {
  const parts = [
    translate(job.kind === "days" ? "adm.bulk.cnt_applied" : "adm.bulk.cnt_delivered", { n: job.applied }),
    translate("adm.bulk.cnt_skipped", { n: job.skipped }),
    translate("adm.bulk.cnt_failed", { n: job.failed }),
  ];
  if (job.unknown > 0) parts.push(translate("adm.bulk.cnt_unknown", { n: job.unknown }));
  if (job.verify_flagged > 0) parts.push(translate("adm.bulk.cnt_verify", { n: job.verify_flagged }));
  return parts.join(" · ");
}

/** Текст компенсации по умолчанию — чтобы не сочинять его в спешке во время простоя. */
export function compensationText(days: number, lang?: Lang): string {
  const l = lang ?? getActiveLang();
  const daysLabel = translate(
    pluralFor(l, days, "adm.bulk.days_one", "adm.bulk.days_few", "adm.bulk.days_many"),
    { n: days },
    l,
  );
  return translate("adm.bulk.compensation", { days: daysLabel }, l);
}

/** Идентификатор запуска. Один на набор параметров: повтор после обрыва сети уходит
 *  с тем же — бэкенд узнаёт его и не создаёт вторую задачу. */
export function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // Старые браузеры без randomUUID: uuid4 из getRandomValues.
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = ((b[6] ?? 0) & 0x0f) | 0x40;
  b[8] = ((b[8] ?? 0) & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
