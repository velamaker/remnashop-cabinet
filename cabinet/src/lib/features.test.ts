import { describe, expect, it } from "vitest";

import type { Appearance } from "@/api/appearance";
import { canFeature } from "./features";

const base = { brand_name: "X" } as Appearance;

describe("canFeature", () => {
  // Эти четыре случая — гарантия того, что установка с нашим ботом (он поля
  // `features` не отдаёт) ведёт себя ровно как раньше. Если тут что-то станет
  // false, кабинет у действующих пользователей начнёт прятать рабочие разделы.
  it("нет оформления — возможность доступна", () => {
    expect(canFeature(null, "gift")).toBe(true);
    expect(canFeature(undefined, "gift")).toBe(true);
  });

  it("оформление без поля features — доступна", () => {
    expect(canFeature(base, "gift")).toBe(true);
  });

  it("пустой список возможностей — доступна", () => {
    expect(canFeature({ ...base, features: {} }, "gift")).toBe(true);
  });

  it("другого ключа нет в списке — доступна", () => {
    expect(canFeature({ ...base, features: { push: false } }, "gift")).toBe(true);
  });

  it("выключена только явным false", () => {
    expect(canFeature({ ...base, features: { gift: false } }, "gift")).toBe(false);
  });

  it("true оставляет доступной", () => {
    expect(canFeature({ ...base, features: { gift: true } }, "gift")).toBe(true);
  });
});

// Функции, которым нужен бот новее 1.3.8 (FEATURE_NEEDS_BOT). Кабинет бывает новее
// бота — обновили только кабинет или он на отдельном сервере, — и тогда пункт,
// опция или тумблер упирались в 404 или «сохранялись» без сохранения.
describe("canFeature: функции, которым нужен новый бот", () => {
  const oldBot = base; // бот 1.3.8: поля bot_capabilities нет
  const newBot = { ...base, bot_capabilities: ["bulk_jobs", "device_upsell"] } as Appearance;

  it("бот без поля bot_capabilities — спрятано", () => {
    expect(canFeature(oldBot, "bulk_days")).toBe(false);
    expect(canFeature(oldBot, "bulk_message")).toBe(false);
    expect(canFeature(oldBot, "device_upsell")).toBe(false);
  });

  it("бот прислал токен — доступно", () => {
    expect(canFeature(newBot, "bulk_days")).toBe(true);
    expect(canFeature(newBot, "bulk_message")).toBe(true);
    expect(canFeature(newBot, "device_upsell")).toBe(true);
  });

  it("токен другой функции не открывает эту", () => {
    const partial = { ...base, bot_capabilities: ["bulk_jobs"] } as Appearance;
    expect(canFeature(partial, "bulk_days")).toBe(true);
    expect(canFeature(partial, "device_upsell")).toBe(false);
  });

  it("явный false чужого бэкенда главнее токена", () => {
    const both = { ...newBot, features: { bulk_days: false } } as Appearance;
    expect(canFeature(both, "bulk_days")).toBe(false);
  });

  it("чужой бэкенд (features) решает сам — токены нашего бота не нужны", () => {
    const adapter = { ...base, features: { push: false } } as Appearance;
    expect(canFeature(adapter, "bulk_days")).toBe(true);
    expect(canFeature({ ...base, features: { device_upsell: false } }, "device_upsell")).toBe(false);
  });

  it("без оформления новое не показываем, прежнее — как было", () => {
    expect(canFeature(null, "bulk_days")).toBe(false);
    expect(canFeature(undefined, "device_upsell")).toBe(false);
    expect(canFeature(null, "gift")).toBe(true);
  });

  it("функции не из манифеста у старого бота — как раньше", () => {
    expect(canFeature(oldBot, "gift")).toBe(true);
    expect(canFeature(oldBot, "purchase")).toBe(true);
    expect(canFeature(oldBot, "admin")).toBe(true);
  });

  it("мусор вместо списка — как бота без поля", () => {
    const junk = { ...base, bot_capabilities: "bulk_jobs" as unknown as string[] } as Appearance;
    expect(canFeature(junk, "bulk_days")).toBe(false);
  });
});
