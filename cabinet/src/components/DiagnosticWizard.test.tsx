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
const createTicket = vi.fn();
vi.mock("@/api/support", () => ({ supportApi: { create: (s: string, b: string) => createTicket(s, b) } }));

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

function device(over: Record<string, unknown> = {}) {
  return {
    hwid: "hw-1",
    platform: "ios",
    device_model: "iPhone 14",
    os_version: "iOS 17.4",
    user_agent: "v2RayTun/2.1/ios/hw-1",
    created_at: null,
    updated_at: null,
    ...over,
  };
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  current.mockResolvedValue(sub());
  devices.mockResolvedValue({ current_count: 1, max_count: 3, devices: [] });
  serviceStatus.mockResolvedValue({ nodes: [{ online: true }], all_operational: true });
  createTicket.mockReset();
  createTicket.mockResolvedValue({ id: 7 });
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

/**
 * ЧТО ЗАПЕРТО ЗДЕСЬ — «паспорт обращения». Раньше тикет уходил владельцу с одними
 * результатами проверок и строкой «Опишите проблему подробнее», то есть без
 * главного: на каком аппарате и что именно не открывается. Теперь мастер спрашивает
 * это перед отправкой, а ответы обязаны доехать в тело тикета.
 */
describe("самодиагностика: что уходит в тикет", () => {
  it("без ответов тикет не отправить", async () => {
    await run();
    const btn = screen.getByText(ru("diag.ticket.create")).closest("button")!;
    expect(btn.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(ru("diag.ask.need"))).toBeTruthy();
  });

  it("аппарат предлагается свой — на кнопке модель и приложение", async () => {
    // Версия ОС на кнопке лишняя (подпись и так длинная), но в тикет уходит
    // целиком — это проверяет следующий кейс.
    devices.mockResolvedValue({ current_count: 1, max_count: 3, devices: [device()] });
    await run();
    expect(screen.getByText("iPhone 14 · v2RayTun")).toBeTruthy();
  });

  it("устройство и «что не работает» уходят в тему и тело тикета", async () => {
    devices.mockResolvedValue({ current_count: 1, max_count: 3, devices: [device()] });
    await run();
    fireEvent.click(screen.getByText(ru("diag.prob.instagram")));
    fireEvent.click(screen.getByText(ru("diag.prob.youtube")));
    fireEvent.click(screen.getByText(ru("diag.ticket.create")));
    await waitFor(() => expect(createTicket).toHaveBeenCalled());

    const [subject, body] = createTicket.mock.calls[0]!;
    expect(subject).toContain("Instagram");
    expect(subject).toContain("YouTube");
    expect(body).toContain(`${ru("diag.ticket.f.device")}: iPhone 14 · v2RayTun · iOS 17.4`);
    expect(body).toContain(`${ru("diag.ticket.f.problems")}: Instagram, YouTube`);
    expect(body).toContain(ru("diag.ticket.f.checks"));
    expect(body).toContain("problems=instagram,youtube");
    expect(body).toContain("app=v2RayTun");
  });

  it("устройств в панели нет — спрашиваем платформу", async () => {
    await run();
    fireEvent.click(screen.getByText(ru("diag.plat.android")));
    fireEvent.click(screen.getByText(ru("diag.prob.noconnect")));
    fireEvent.click(screen.getByText(ru("diag.ticket.create")));
    await waitFor(() => expect(createTicket).toHaveBeenCalled());

    const [, body] = createTicket.mock.calls[0]!;
    expect(body).toContain(`${ru("diag.ticket.f.device")}: ${ru("diag.plat.android")}`);
    expect(body).toContain("platform=android");
  });

  it("«другое устройство» — свободный ответ, и он попадает в тикет", async () => {
    await run();
    fireEvent.click(screen.getByText(ru("diag.ask.deviceOther")));
    fireEvent.change(screen.getByPlaceholderText(ru("diag.ask.devicePh")), {
      target: { value: "роутер Keenetic" },
    });
    fireEvent.click(screen.getByText(ru("diag.prob.sites")));
    fireEvent.change(screen.getByPlaceholderText(ru("diag.ask.commentPh")), {
      target: { value: "перестало работать вчера" },
    });
    fireEvent.click(screen.getByText(ru("diag.ticket.create")));
    await waitFor(() => expect(createTicket).toHaveBeenCalled());

    const [, body] = createTicket.mock.calls[0]!;
    expect(body).toContain(`${ru("diag.ticket.f.device")}: роутер Keenetic`);
    expect(body).toContain(`${ru("diag.ticket.f.comment")}: перестало работать вчера`);
  });
});
