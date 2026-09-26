import { describe, it, expect } from "vitest";
import { safeInternalPath, safeExternalUrl, withNext } from "./nav";

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

// Набор обходов проверки «один ведущий слэш». Тот же список — в серверной
// admin_src/tests/test_oidc_next.py: правило одно, и держат его оба конца.
const BYPASSES: Array<[string, string]> = [
  // Парсер URL выкидывает \t \n \r из любого места: `/\t/evil.com` → `//evil.com`.
  // Реальный путь атаки: ?next=%2F%09%2Fevil.com → регистрация → navigate() →
  // react-router ловит SecurityError в pushState и уходит через location.assign.
  ["табуляция", "/\t/evil.com"],
  ["перевод строки", "/\n/evil.com"],
  ["возврат каретки", "/\r/evil.com"],
  ["NUL", "/\u0000/evil.com"],
  ["табуляция в конце пути", "/billing\t"],
  ["пробел в начале", " /devices"],
  ["неразрывный пробел", "/\u00a0/evil.com"],
  ["разделитель строк U+2028", "/\u2028/evil.com"],
  ["BOM", "\ufeff/devices"],
  ["protocol-relative", "//evil.com"],
  ["слэш-бэкслэш", "/\\evil.com"],
  ["закодированный слэш", "/%2F/evil"],
  ["закодированный слэш, строчные", "/%2f/evil"],
  ["закодированный бэкслэш", "/%5C/evil"],
  ["внешний адрес", "https://evil.com"],
  ["javascript:", "javascript:alert(1)"],
  ["javascript: с пробелом", " javascript:alert(1)"],
  // Парсер схлопывает «.» и «..»: без этой проверки нормализованный путь
  // `//evil.com` сам стал бы адресом чужого хоста.
  ["точка", "/.//evil.com"],
  ["две точки", "/..//evil.com"],
  ["точки через %2e", "/%2e%2e//evil.com"],
  ["точка через %2E", "/%2E//evil.com"],
];

describe("safeInternalPath — обходы через разбор URL", () => {
  it.each(BYPASSES)("%s → на главную", (_name, raw) => {
    expect(safeInternalPath(raw)).toBe("/");
  });

  it("что бы ни пришло, браузер не прочитает итог как чужой хост", () => {
    const origin = window.location.origin;
    for (const [, raw] of BYPASSES) {
      const out = safeInternalPath(raw);
      expect(out.startsWith("//")).toBe(false);
      expect(new URL(out, origin).origin).toBe(origin);
    }
  });

  it("обычные пути с ? и # проходят без изменений", () => {
    const gift = "/billing?promo=GIFT-" + "A".repeat(32);
    expect(safeInternalPath(gift)).toBe(gift);
    expect(safeInternalPath("/billing?promo=X&from=gift#top")).toBe("/billing?promo=X&from=gift#top");
    expect(safeInternalPath("/devices#add")).toBe("/devices#add");
    expect(safeInternalPath("/admin/users?q=a%20b")).toBe("/admin/users?q=a%20b");
  });

  it("сегменты . и .. не пускаем вовсе — как и сервер", () => {
    // Сервер путь не нормализует и режет их сразу; держим то же правило, чтобы
    // адрес, принятый кабинетом, не отбрасывался на OIDC-входе.
    expect(safeInternalPath("/a/../billing")).toBe("/");
    expect(safeInternalPath("/./devices")).toBe("/");
    expect(safeInternalPath("/devices/%2E")).toBe("/");
    // А точка внутри имени — обычный путь.
    expect(safeInternalPath("/info/v1.2?x=..")).toBe("/info/v1.2?x=..");
  });

  it("отдаёт нормализованный путь — то, что прочитает браузер", () => {
    // Кириллицу в запросе браузер всё равно закодирует; отдаём уже его видом,
    // чтобы в ?next= дальше ушла ровно та строка, которую потом разберут.
    expect(safeInternalPath("/info?q=тест")).toBe("/info?q=%D1%82%D0%B5%D1%81%D1%82");
  });
});

describe("withNext — адрес с сохранённым next", () => {
  it("добавляет next, закодированный целиком", () => {
    expect(withNext("/register", "/billing?promo=GIFT-X")).toBe(
      "/register?next=%2Fbilling%3Fpromo%3DGIFT-X",
    );
  });

  it("к адресу с запросом — через &", () => {
    expect(withNext("/login?staff=1", "/devices")).toBe("/login?staff=1&next=%2Fdevices");
  });

  it("главную и пустой next не пишет", () => {
    expect(withNext("/login", "/")).toBe("/login");
    expect(withNext("/login", null)).toBe("/login");
  });

  it("чужой адрес не протаскивает", () => {
    expect(withNext("/login", "/\t/evil.com")).toBe("/login");
    expect(withNext("/api/auth/telegram/oidc/start", "//evil.com")).toBe("/api/auth/telegram/oidc/start");
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
