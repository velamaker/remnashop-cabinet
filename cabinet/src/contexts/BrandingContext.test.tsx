import { describe, it, expect, beforeEach } from "vitest";
import { readCache } from "./BrandingContext";

// Кэш оформления держит бренд и логотип, пока бот недоступен. Списки возможностей
// из него не берутся: кэш мог остаться от другого бэкенда или от бота до отката, и
// кабинет показал бы функции, которых у работающего бота уже нет (или спрятал бы
// рабочие). Решают только свежие ответы сервера.

const KEY = "cabinet-appearance";

beforeEach(() => {
  localStorage.clear();
});

describe("readCache", () => {
  it("выкидывает features и bot_capabilities, остальное оставляет", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({
        brand_name: "Бренд",
        accent: "#123456",
        features: { gift: false },
        bot_capabilities: ["bulk_jobs", "renewal_discount"],
      }),
    );
    const cached = readCache();
    expect(cached?.brand_name).toBe("Бренд");
    expect(cached?.accent).toBe("#123456");
    expect(cached && "features" in cached).toBe(false);
    expect(cached && "bot_capabilities" in cached).toBe(false);
  });

  it("пустой или битый кэш — null", () => {
    expect(readCache()).toBeNull();
    localStorage.setItem(KEY, "{не json");
    expect(readCache()).toBeNull();
  });
});
