import { describe, it, expect } from "vitest";
import type { BulkJob } from "@/api/admin";
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

describe("массовые задачи: помощники", () => {
  it("«Не получат» не выводит нулевые категории и подсказывает про галочки", () => {
    expect(skippedLines({ NO_SUBSCRIPTION: 780, EXPIRED: 0, RESERVE: 1, LIMITED: 1, STAFF: 0 })).toEqual([
      "без подписки — 780",
      "на резервном доступе (не оплачено) — 1",
      "исчерпан трафик — 1 (включите галочку выше, чтобы добавить)",
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
    expect(jobCounters(job())).toBe("добавлено 6 · пропущено 3 · ошибок 1");
    expect(jobCounters(job({ kind: "message", unknown: 2, verify_flagged: 1 }))).toBe(
      "доставлено 6 · пропущено 3 · ошибок 1 · неизвестно 2 · проверить вручную: 1",
    );
  });

  it("текст компенсации склоняет дни", () => {
    expect(compensationText(1)).toContain("добавили 1 день к вашей подписке");
    expect(compensationText(3)).toContain("добавили 3 дня к вашей подписке");
  });

  it("request_id — uuid и каждый раз новый", () => {
    const a = newRequestId();
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
    expect(newRequestId()).not.toBe(a);
  });
});
