import { describe, expect, it } from "vitest";
import { APPS } from "@/data/apps";

/**
 * Страница `public/connect.html` открывает приложение по ссылке из фрагмента и
 * пропускает только известные схемы. Свой список схем там ЖИВЁТ ОТДЕЛЬНО от
 * `nav.ts`: это статичный файл без сборки и импортов.
 *
 * 18.09 из-за этого «Открыть приложение» не работало для INCY: схему `incy://`
 * добавили в приложения и в nav.ts, а в connect.html — нет. Человек видел
 * «Открываем приложение…», и дальше ничего не происходило: ни авто-переход, ни
 * кнопка (её href оставался «#»).
 *
 * Тест держит оба списка в согласии с каталогом приложений.
 */

function schemesFrom(source: string): Set<string> {
  const match = /\^\(([^)]+)\)/.exec(source);
  const group = match?.[1];
  if (!group) throw new Error("не нашёл список схем");
  return new Set(group.toLowerCase().split("|"));
}

// Оба списка читаем ИСХОДНИКАМИ через import.meta.glob (как сторож возможностей
// бота): connect.html не собирается вовсе, а в nav.ts список приватный —
// экспортировать его только ради теста значит менять рабочий код. Через node:fs
// делать нельзя: сборка кабинета гоняет tsc без типов Node и падала бы.
function raw(files: Record<string, string>, what: string): string {
  const [source] = Object.values(files);
  if (!source) throw new Error(`не прочитал ${what}`);
  return source;
}

const connectHtml = raw(
  import.meta.glob<string>("../../public/connect.html", { query: "?raw", import: "default", eager: true }),
  "public/connect.html",
);
const navSource = raw(
  import.meta.glob<string>("./nav.ts", { query: "?raw", import: "default", eager: true }),
  "lib/nav.ts",
);

const appSchemes = [
  ...new Set(
    APPS.flatMap((app) => {
      const link = app.deepLink?.("SUB") ?? "";
      const scheme = /^([a-z0-9+.-]+):\/\//i.exec(link)?.[1];
      return scheme ? [scheme.toLowerCase()] : [];
    }),
  ),
];

describe("схемы приложений в странице «Открываем приложение»", () => {
  it("каталог приложений вообще даёт схемы", () => {
    expect(appSchemes.length).toBeGreaterThan(3);
    expect(appSchemes).toContain("incy");
  });

  it("connect.html пропускает каждую схему из каталога", () => {
    const allowed = schemesFrom(connectHtml);
    const missing = appSchemes.filter((s) => !allowed.has(s));
    expect(missing, `нет в connect.html: ${missing.join(", ")}`).toEqual([]);
  });

  it("nav.ts пропускает каждую схему из каталога", () => {
    const allowed = schemesFrom(navSource);
    const missing = appSchemes.filter((s) => !allowed.has(s));
    expect(missing, `нет в nav.ts: ${missing.join(", ")}`).toEqual([]);
  });

  it("опасные схемы по-прежнему отвергаются", () => {
    const allowed = schemesFrom(connectHtml);
    for (const bad of ["javascript", "data", "blob", "vbscript", "file"]) {
      expect(allowed.has(bad)).toBe(false);
    }
  });
});
