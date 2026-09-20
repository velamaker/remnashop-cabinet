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
  // Кабинет ПОЛЬЗОВАТЕЛЯ переведён целиком: дыра здесь — это чужой язык на экране
  // человека, который платит. Админка — только русский и английский (решение
  // владельца 20.09: «админка на русском и англ, больше не надо»), поэтому ключи
  // adm.* в прочих языках не живут вовсе: translate отдаёт русский по цепочке
  // «язык → ru → ключ».
  const userKeys = Object.keys(DICT.ru).filter((k) => !k.startsWith("adm."));

  it("во всех языках столько же ПОЛЬЗОВАТЕЛЬСКИХ ключей, сколько в ru (без дыр)", () => {
    for (const lang of Object.keys(DICT) as (keyof typeof DICT)[]) {
      const missing = userKeys.filter((k) => !(k in DICT[lang]));
      expect(missing, `язык ${lang} без ключей: ${missing.slice(0, 5).join(", ")}`).toHaveLength(0);
    }
  });

  it("ключей админки нет там, где их нет в русском (опечатки в ключах)", () => {
    const ruAdmin = new Set(Object.keys(DICT.ru).filter((k) => k.startsWith("adm.")));
    for (const lang of Object.keys(DICT) as (keyof typeof DICT)[]) {
      const orphans = Object.keys(DICT[lang]).filter((k) => k.startsWith("adm.") && !ruAdmin.has(k));
      expect(orphans, `язык ${lang}: ключи без русского оригинала: ${orphans.slice(0, 5).join(", ")}`).toHaveLength(0);
    }
  });

  it("админка живёт только в русском и английском", () => {
    for (const lang of Object.keys(DICT) as (keyof typeof DICT)[]) {
      if (lang === "ru" || lang === "en") continue;
      const extra = Object.keys(DICT[lang]).filter((k) => k.startsWith("adm."));
      expect(extra, `язык ${lang}: лишние ключи админки: ${extra.slice(0, 5).join(", ")}`).toHaveLength(0);
    }
  });
});

// Ключ, которого нет в словаре, translate() показывает как есть: на кнопке оплаты
// вышло бы «billing.changeConfirmYes». Словарь у владельца живёт отдельной
// локальной копией, и новые ключи туда переносят руками. Забытый перенос тесты
// поймают здесь, а не пользователь на странице. Смотрим только буквальные ключи:
// t("область.строка") и translate("…"), в том числе с переносом строки.
const SOURCES = import.meta.glob<string>(
  ["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}", "!/src/test/**"],
  { query: "?raw", import: "default", eager: true },
);
const LITERAL_KEY = /\b(?:t|translate)\(\s*(["'`])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\1/g;

function literalKeys(): Map<string, string[]> {
  const used = new Map<string, string[]>();
  for (const [file, text] of Object.entries(SOURCES)) {
    for (const m of text.matchAll(LITERAL_KEY)) {
      const key = m[2]!;
      used.set(key, [...(used.get(key) ?? []), file]);
    }
  }
  return used;
}

describe("i18n ключи из кода", () => {
  it("каждый буквальный ключ из t()/translate() есть в ru", () => {
    const used = literalKeys();
    // Страховка от пустого прохода: если поиск файлов сломается, тест не должен
    // молча стать зелёным. Ключи блока устройств и подтверждения смены тарифа
    // проверяем поимённо: их забыть при переносе дороже всего.
    expect(used.size).toBeGreaterThan(100);
    for (const key of [
      "deviceUpsell.title",
      "billing.changeWarn",
      "billing.changeConfirmYes",
      "billing.changeCarry",
      "billing.changeCarryConfirm",
      "deviceUpsell.carryDays",
    ]) {
      expect(used.has(key), `поиск не нашёл ${key} в коде`).toBe(true);
    }
    const missing = [...used.entries()]
      .filter(([key]) => !(key in DICT.ru))
      .map(([key, files]) => `${key} (${[...new Set(files)].join(", ")})`);
    expect(missing, `в словаре ru нет ключей:\n${missing.join("\n")}`).toEqual([]);
  });
});
