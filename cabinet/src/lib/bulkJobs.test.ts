import { describe, it, expect, beforeEach } from "vitest";
import type { BulkJob } from "@/api/admin";
import { setActiveLang, translate } from "@/i18n/translate";
import { compensationText, estimateMinutes, isActive, jobCounters, newRequestId, skippedLines } from "./bulkJobs";

const job = (over: Partial<BulkJob> = {}): BulkJob => ({
  id: 1,
  kind: "days",
  status: "COMPLETED",
  created_by: "@owner",
  created_at: null,
  started_at: null,
  finished_at: null,
  total: 10,
  done: 10,
  applied: 6,
  skipped: 3,
  failed: 1,
  unknown: 0,
  verify_flagged: 0,
  params: { days: 3, include_trial: false, include_limited: false, channels: null, text_preview: null },
  pause_reason: null,
  parent_job_id: null,
  child_job_id: null,
  breakdown: {},
  ...over,
});

// Подписи живут в словаре (ключи adm.bulk.*), а не в коде: сверяемся с тем же
// словарём по-русски, иначе тест проверял бы копию строки, а не помощника.
const ru = (key: string, vars: Record<string, string | number> = {}) => translate(key, vars, "ru");

beforeEach(() => {
  setActiveLang("ru"); // помощники чистые и берут язык модульно
});

describe("массовые задачи: помощники", () => {
  it("«Не получат» не выводит нулевые категории и подсказывает про галочки", () => {
    expect(skippedLines({ NO_SUBSCRIPTION: 780, EXPIRED: 0, RESERVE: 1, LIMITED: 1, STAFF: 0 })).toEqual([
      ru("adm.bulk.skip_no_subscription", { n: 780 }),
      ru("adm.bulk.skip_reserve", { n: 1 }),
      ru("adm.bulk.skip_limited", { n: 1 }),
    ]);
    expect(skippedLines(undefined)).toEqual([]);
  });

  it("оценка времени: дни — около секунды на человека, сообщение — в пять раз быстрее", () => {
    expect(estimateMinutes("days", 1)).toBe(1);
    expect(estimateMinutes("days", 600)).toBe(10);
    expect(estimateMinutes("message", 600)).toBe(2);
    expect(estimateMinutes("message", 0)).toBe(1);
  });

  it("опрашиваем только идущие задачи", () => {
    expect(isActive("PROCESSING")).toBe(true);
    expect(isActive("CANCELING")).toBe(true);
    expect(isActive("PAUSED")).toBe(false);
    expect(isActive("COMPLETED")).toBe(false);
  });

  it("счётчики задачи: неизвестные и «проверить вручную» — только когда есть", () => {
    expect(jobCounters(job())).toBe(
      [ru("adm.bulk.cnt_applied", { n: 6 }), ru("adm.bulk.cnt_skipped", { n: 3 }), ru("adm.bulk.cnt_failed", { n: 1 })].join(" · "),
    );
    expect(jobCounters(job({ kind: "message", unknown: 2, verify_flagged: 1 }))).toBe(
      [
        ru("adm.bulk.cnt_delivered", { n: 6 }),
        ru("adm.bulk.cnt_skipped", { n: 3 }),
        ru("adm.bulk.cnt_failed", { n: 1 }),
        ru("adm.bulk.cnt_unknown", { n: 2 }),
        ru("adm.bulk.cnt_verify", { n: 1 }),
      ].join(" · "),
    );
  });

  it("текст компенсации склоняет дни по языку", () => {
    const text = (days: string, lang: "ru" | "en") => translate("adm.bulk.compensation", { days }, lang);
    expect(compensationText(1, "ru")).toBe(text(ru("adm.bulk.days_one", { n: 1 }), "ru"));
    expect(compensationText(3, "ru")).toBe(text(ru("adm.bulk.days_few", { n: 3 }), "ru"));
    // По-английски правило другое: «1 day», но «3 days», а не «3 день».
    expect(compensationText(3, "en")).toBe(text(translate("adm.bulk.days_many", { n: 3 }, "en"), "en"));
  });

  it("request_id — uuid и каждый раз новый", () => {
    const a = newRequestId();
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
    expect(newRequestId()).not.toBe(a);
  });
});
