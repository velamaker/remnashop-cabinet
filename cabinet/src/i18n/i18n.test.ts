import { describe, it, expect } from "vitest";
import { translate, interpolate } from "./translate";
import { enabledLanguages, isLang, LANGUAGES } from "./config";
import { DICTIONARIES as DICT } from "./dictionaries";

describe("i18n translate", () => {
  it("отдаёт значение выбранного языка", () => {
    expect(translate("billing.title", {}, "ru")).toBe("Тарифы");
    expect(translate("billing.title", {}, "en")).toBe("Plans");
  });

  it("несуществующий ключ → возвращает сам ключ (конец фолбэк-цепочки)", () => {
    expect(translate("__no_such_key__", {}, "en")).toBe("__no_such_key__");
  });

  it("подставляет переменные {var}", () => {
    expect(interpolate("{n} дн.", { n: 5 })).toBe("5 дн.");
    expect(translate("ref.earnedDays", { n: 3 }, "ru")).toBe("3 дн.");
  });
});

describe("i18n config", () => {
  it("isLang валидирует коды", () => {
    expect(isLang("ru")).toBe(true);
    expect(isLang("xx")).toBe(false);
    expect(isLang(null)).toBe(false);
  });

  it("enabledLanguages: null/пусто → все языки", () => {
    expect(enabledLanguages(null)).toHaveLength(LANGUAGES.length);
    expect(enabledLanguages([])).toHaveLength(LANGUAGES.length);
  });

  it("enabledLanguages фильтрует и всегда включает ru", () => {
    const codes = enabledLanguages(["en"]).map((l) => l.code);
    expect(codes).toContain("ru");
    expect(codes).toContain("en");
    expect(codes).not.toContain("tr");
  });
});

describe("i18n полнота словарей", () => {
  it("во всех языках столько же ключей, сколько в ru (без дыр)", () => {
    const ruKeys = Object.keys(DICT.ru);
    for (const lang of Object.keys(DICT) as (keyof typeof DICT)[]) {
      const missing = ruKeys.filter((k) => !(k in DICT[lang]));
      expect(missing, `язык ${lang} без ключей: ${missing.slice(0, 5).join(", ")}`).toHaveLength(0);
    }
  });
});
