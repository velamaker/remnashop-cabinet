import { describe, expect, it } from "vitest";

import { BOT_CAPABILITIES, FEATURE_NEEDS_BOT, PAGE_NEEDS_BOT, type BotCap } from "./botCapabilities";
import { BASELINE_1_3_8_PATHS } from "./botCapabilities.baseline-1.3.8";
import { PATH_GRACEFUL, PATH_NEEDS_BOT, extractApiPaths } from "./botCapabilities.paths";

// Сторож «не забыть». Кабинет бывает новее бота, и всё, что кабинет зовёт сверх
// v1.3.8, у старого бота ответит 404. Каждый такой путь обязан получить решение:
// либо функция прячется по токену, либо кабинет переживает отсутствие сам (с
// причиной). Добавили ручку и забыли — падает здесь, а не у оператора, обновившего
// только кабинет. Правило, КАКУЮ таблицу выбрать, — в шапке botCapabilities.ts.

const SOURCES = import.meta.glob<string>(
  [
    "/src/**/*.{ts,tsx}",
    "!/src/**/*.test.{ts,tsx}",
    "!/src/test/**",
    // Сами таблицы сторожа путями не пользуются, а лишь перечисляют их.
    "!/src/lib/botCapabilities.paths.ts",
    "!/src/lib/botCapabilities.baseline-1.3.8.ts",
  ],
  { query: "?raw", import: "default", eager: true },
);

// Список бота и конфигурация адаптера живут вне кабинета; CI делает полный checkout.
const BOT_FILE = import.meta.glob<string>("../../../admin_src/src/web/cabinet_capabilities.py", {
  query: "?raw",
  import: "default",
  eager: true,
});
const ADAPTER_COMPOSE = import.meta.glob<string>("../../../adapter/compose.py", {
  query: "?raw",
  import: "default",
  eager: true,
});

const current = extractApiPaths(SOURCES);
const baseline = new Set(BASELINE_1_3_8_PATHS);

describe("сторож путей: всё новое после v1.3.8 классифицировано", () => {
  it("поиск не прошёл впустую", () => {
    // Сломается поиск файлов или регулярка — тест не должен молча позеленеть.
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    expect(current.size).toBeGreaterThan(200);
    expect(baseline.size).toBeGreaterThan(200);
    for (const path of [
      "GET /api/appearance",
      "GET /api/admin/users/bulk/jobs",
      "POST /api/admin/renewal-discount/test-send",
      "ANY /api/admin/appearance/logo",
    ]) {
      expect(current.has(path), `извлекатель не нашёл ${path}`).toBe(true);
    }
  });

  it("каждый новый путь — в PATH_NEEDS_BOT или PATH_GRACEFUL", () => {
    const fresh = [...current].filter((p) => !baseline.has(p)).sort();
    const unclassified = fresh.filter((p) => !(p in PATH_NEEDS_BOT) && !(p in PATH_GRACEFUL));
    expect(
      unclassified,
      "Новые пути API без решения. Нужен новый бот и вход в функцию виден до ответа — " +
        "PATH_NEEDS_BOT + токен; кабинет сам переживает 404 — PATH_GRACEFUL с причиной " +
        "(botCapabilities.paths.ts, правило — шапка botCapabilities.ts)",
    ).toEqual([]);
  });

  it("мёртвых записей нет: путь из таблиц кабинет ещё зовёт и его не было в v1.3.8", () => {
    for (const path of [...Object.keys(PATH_NEEDS_BOT), ...Object.keys(PATH_GRACEFUL)]) {
      expect(current.has(path), `${path} кабинет больше не зовёт — уберите запись`).toBe(true);
      expect(baseline.has(path), `${path} был и в v1.3.8 — старый бот его умеет`).toBe(false);
    }
  });

  it("путь не в двух таблицах сразу, у «переживает» есть причина", () => {
    for (const path of Object.keys(PATH_NEEDS_BOT)) expect(path in PATH_GRACEFUL).toBe(false);
    for (const why of Object.values(PATH_GRACEFUL)) expect(why.trim().length).toBeGreaterThan(10);
  });

  it("токен каждого «нужен бот» пути действительно что-то прячет", () => {
    // Иначе путь помечен «нужен бот», а вход в функцию по-прежнему виден.
    const hiding = new Set<BotCap>([...Object.values(FEATURE_NEEDS_BOT), ...Object.values(PAGE_NEEDS_BOT)]);
    for (const [path, cap] of Object.entries(PATH_NEEDS_BOT)) {
      expect(hiding.has(cap), `${path}: токен ${cap} ничего не прячет`).toBe(true);
    }
  });
});

describe("манифест кабинета совпадает со списком бота", () => {
  const text = Object.values(BOT_FILE)[0] ?? "";

  it("файл бота прочитан", () => {
    expect(text).toContain("CABINET_CAPABILITIES");
  });

  it("токены — те же и в том же порядке", () => {
    // Строки `    "токен",` — формат заперт и в pytest (update.sh читает его sed'ом).
    const bot = [...text.matchAll(/^ {4}"([a-z0-9_]+)",$/gm)].map((m) => m[1]);
    expect(bot).toEqual(Object.keys(BOT_CAPABILITIES));
  });

  it("изменения «только в боте» кабинетом не перечисляются", () => {
    const botOnly = [...text.matchAll(/^ {4}"([a-z0-9_]+)": "[^"]*",$/gm)].map((m) => m[1]!);
    expect(botOnly.length).toBeGreaterThan(0);
    for (const token of botOnly) expect(Object.keys(BOT_CAPABILITIES)).not.toContain(token);
  });
});

describe("адаптер «Бедолаги» объявляет каждую возможность, зависящую от бота", () => {
  // Поверх «Бедолаги» решает адаптер (features), и «нет ключа = умеет». Ключ из
  // FEATURE_NEEDS_BOT, забытый в адаптере, показал бы функцию нашего бота чужому админу.
  const text = Object.values(ADAPTER_COMPOSE)[0] ?? "";
  const block = (name: string) => {
    const start = text.indexOf(`${name}: dict[str,`);
    expect(start, `${name} не найден в adapter/compose.py`).toBeGreaterThan(-1);
    return text.slice(start, text.indexOf("\n}\n", start));
  };

  it("ключ есть в FEATURE_REQUIREMENTS или FEATURE_OVERRIDES", () => {
    const declared = block("FEATURE_REQUIREMENTS") + block("FEATURE_OVERRIDES");
    for (const key of Object.keys(FEATURE_NEEDS_BOT)) {
      expect(declared, `ключ ${key} не объявлен в adapter/compose.py`).toContain(`"${key}":`);
    }
  });
});
