import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Самодиагностика «VPN не работает».
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ — проверка подключения, которой не хватало. Замер по боевой базе:
 * из 78 истёкших пробных 37 не подключились НИ РАЗУ, а 15 успели завести устройство и
 * всё равно не пошли дальше. Для них ответ «подписка активна, трафик есть, устройства
 * есть» бесполезен: у них просто не настроено приложение, и сказать об этом должен
 * именно этот экран.
 *
 * Данные берутся из ответа подписки, лишних запросов нет.
 */

const current = vi.fn();
const devices = vi.fn();
const serviceStatus = vi.fn();

vi.mock("@/api/subscription", () => ({
  subscriptionApi: {
    current: () => current(),
    devices: () => devices(),
    serviceStatus: () => serviceStatus(),
    reissue: vi.fn(),
  },
}));
vi.mock("@/api/support", () => ({ supportApi: { create: vi.fn() } }));

const { DiagnosticWizard } = await import("./DiagnosticWizard");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const DAY = 24 * 3600 * 1000;

function sub(over: Record<string, unknown> = {}) {
  return {
    status: "ACTIVE",
    expire_at: new Date(Date.now() + 30 * DAY).toISOString(),
    traffic_limit: 0,
    used_traffic_bytes: 5 * 1024 ** 3,
    lifetime_used_traffic_bytes: 5 * 1024 ** 3,
    online_at: new Date(Date.now() - 3600 * 1000).toISOString(),
    url: "https://sub.example/abc",
    ...over,
  };
}

async function run() {
  render(
    <MemoryRouter>
      <I18nProvider>
        <DiagnosticWizard />
      </I18nProvider>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByText(ru("diag.check")));
  await waitFor(() => expect(screen.getByText(ru("diag.again"))).toBeTruthy());
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  current.mockResolvedValue(sub());
  devices.mockResolvedValue({ current_count: 1, max_count: 3, devices: [] });
  serviceStatus.mockResolvedValue({ nodes: [{ online: true }], all_operational: true });
});
afterEach(() => cleanup());

describe("самодиагностика: подключался ли человек", () => {
  it("ни одного подключения — это и есть причина, а не «всё хорошо»", async () => {
    current.mockResolvedValue(
      sub({ online_at: null, used_traffic_bytes: 0, lifetime_used_traffic_bytes: 0 }),
    );
    await run();
    expect(screen.getByText(ru("diag.conn.never"))).toBeTruthy();
    expect(screen.getByText(ru("diag.conn.never.hint"))).toBeTruthy();
  });

  it("подключался давно — предупреждение, а не отказ", async () => {
    current.mockResolvedValue(sub({ online_at: new Date(Date.now() - 30 * DAY).toISOString() }));
    await run();
    expect(screen.getByText(/Последнее подключение/)).toBeTruthy();
  });

  it("подключался недавно — проверка пройдена", async () => {
    await run();
    expect(screen.getByText(ru("diag.conn.ok"))).toBeTruthy();
  });

  it("трафик был, но статистика онлайна пустая — не пугаем «никогда»", async () => {
    // Панель показывает online_at не всегда: расход важнее.
    current.mockResolvedValue(sub({ online_at: null }));
    await run();
    expect(screen.queryByText(ru("diag.conn.never"))).toBeNull();
    expect(screen.getByText(ru("diag.conn.ok"))).toBeTruthy();
  });

  it("без подписки проверку подключения не показываем вовсе", async () => {
    current.mockResolvedValue(null);
    await run();
    expect(screen.queryByText(ru("diag.conn.never"))).toBeNull();
    expect(screen.queryByText(ru("diag.conn.ok"))).toBeNull();
  });
});
