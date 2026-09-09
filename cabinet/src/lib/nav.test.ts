import { describe, it, expect } from "vitest";
import { safeInternalPath, safeExternalUrl } from "./nav";

describe("safeInternalPath — защита от open-redirect", () => {
  it("пропускает внутренние пути", () => {
    expect(safeInternalPath("/devices")).toBe("/devices");
    expect(safeInternalPath("/billing?x=1")).toBe("/billing?x=1");
    expect(safeInternalPath("/")).toBe("/");
  });

  it("режет внешние и protocol-relative", () => {
    expect(safeInternalPath("//evil.com")).toBe("/");
    expect(safeInternalPath("http://evil.com")).toBe("/");
    expect(safeInternalPath("https://evil.com")).toBe("/");
  });

  it("режет обход через обратный слэш (CVE react-router)", () => {
    expect(safeInternalPath("/\\evil.com")).toBe("/");
    expect(safeInternalPath("\\\\evil.com")).toBe("/");
    expect(safeInternalPath("/path\\..\\x")).toBe("/");
  });

  it("фолбэк на / при пустом", () => {
    expect(safeInternalPath(null)).toBe("/");
    expect(safeInternalPath(undefined)).toBe("/");
    expect(safeInternalPath("")).toBe("/");
  });
});

describe("схемы приложений и белый список", () => {
  // Кнопка «Подключиться» открывает deep-link через safeExternalUrl. Если схему
  // приложения забыли внести в белый список, ссылка молча отбраковывается: ничего
  // не открывается, а кнопка при этом пишет «Открываем…». Так вышло с INCY —
  // приложение добавили в data/apps.ts, а схему incy:// в nav.ts нет.
  // Этот тест ловит следующее такое приложение до того, как его увидит человек.
  it("каждая схема из data/apps.ts разрешена", async () => {
    const { APPS } = await import("@/data/apps");
    const sub = "https://sub.example.com/abc123";
    const rejected = APPS.filter((app) => !safeExternalUrl(app.deepLink(sub))).map(
      (app) => `${app.id} → ${app.deepLink(sub).split("://")[0]}://`,
    );
    expect(rejected).toEqual([]);
  });
});
