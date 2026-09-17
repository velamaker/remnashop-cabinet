/**
 * Массовые задачи «Пользователей» («+N дней», «Написать») — чистые помощники.
 *
 * Логика «кому» живёт на бэкенде (services/overlay_bulk.py); здесь только то, как
 * её цифры показать админу. Админка только на русском, поэтому строки — прямо тут.
 */
import type { BulkJob, BulkJobStatus } from "@/api/admin";
import { pluralRu } from "@/lib/pluralRu";

/** Кто не получит дни — порядок и подписи блока «Не получат». */
export const SKIP_LINES: { key: string; label: string; hint?: string }[] = [
  { key: "NO_SUBSCRIPTION", label: "без подписки" },
  { key: "EXPIRED", label: "подписка истекла" },
  { key: "RESERVE", label: "на резервном доступе (не оплачено)" },
  { key: "DISABLED", label: "подписка отключена" },
  { key: "UNLIMITED", label: "бессрочная подписка" },
  { key: "TRIAL", label: "пробная подписка", hint: "включите галочку выше, чтобы добавить" },
  { key: "LIMITED", label: "исчерпан трафик", hint: "включите галочку выше, чтобы добавить" },
  { key: "BLOCKED", label: "заблокированы" },
];

/** Строки «Не получат»: нулевые категории не показываем — это шум. */
export function skippedLines(skipped: Record<string, number> | undefined): string[] {
  if (!skipped) return [];
  return SKIP_LINES.filter(({ key }) => (skipped[key] ?? 0) > 0).map(({ key, label, hint }) =>
    hint ? `${label} — ${skipped[key]} (${hint})` : `${label} — ${skipped[key]}`,
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

const STATUS_LABELS: Record<BulkJobStatus, string> = {
  QUEUED: "в очереди",
  PROCESSING: "идёт",
  PAUSED: "на паузе",
  CANCELING: "останавливается",
  COMPLETED: "готово",
  CANCELED: "остановлена",
  ERROR: "ошибка",
};

export function jobStatusLabel(status: BulkJobStatus): string {
  return STATUS_LABELS[status] ?? status;
}

/** Задача ещё может что-то поменять у людей — опрашиваем её прогресс. */
export function isActive(status: BulkJobStatus): boolean {
  return status === "QUEUED" || status === "PROCESSING" || status === "CANCELING";
}

export function jobKindLabel(kind: BulkJob["kind"]): string {
  return kind === "days" ? "Добавление дней" : "Сообщение";
}

/** «добавлено 20 · пропущено 3 · ошибок 0» с хвостом про неизвестные и ручную проверку. */
export function jobCounters(job: BulkJob): string {
  const parts =
    job.kind === "days"
      ? [`добавлено ${job.applied}`, `пропущено ${job.skipped}`, `ошибок ${job.failed}`]
      : [`доставлено ${job.applied}`, `пропущено ${job.skipped}`, `ошибок ${job.failed}`];
  if (job.unknown > 0) parts.push(`неизвестно ${job.unknown}`);
  if (job.verify_flagged > 0) parts.push(`проверить вручную: ${job.verify_flagged}`);
  return parts.join(" · ");
}

/** Текст компенсации по умолчанию — чтобы не сочинять его в спешке во время простоя. */
export function compensationText(days: number): string {
  return (
    "Приносим извинения за недавние перебои в работе сервиса. В качестве компенсации мы добавили " +
    `${days} ${pluralRu(days, "день", "дня", "дней")} к вашей подписке — делать ничего не нужно, ` +
    "срок уже продлён. Спасибо, что остаётесь с нами!"
  );
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
