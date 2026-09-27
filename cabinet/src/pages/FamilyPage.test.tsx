import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";
import { familyVisible, type FamilyResponse } from "@/api/family";

// «Семья» в кабинете: ссылка профиля видна и копируется, двойной клик «Добавить»
// уходит ОДНИМ ключом, удаление — только после подтверждения, чужое/выключенное
// показывает причину, а не кнопки, упирающиеся в ошибку.
const getMock = vi.fn();
const createMock = vi.fn();
const resetMock = vi.fn();
const removeMock = vi.fn();
vi.mock("@/api/family", async () => {
  const real = await vi.importActual<typeof import("@/api/family")>("@/api/family");
  return {
    ...real,
    familyApi: {
      get: () => getMock(),
      summary: () => getMock(),
      create: (body: unknown) => createMock(body),
      resetDevices: (id: number) => resetMock(id),
      remove: (id: number) => removeMock(id),
    },
  };
});
let canFamily = true;
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ appearance: {}, can: (key: string) => key !== "family_profiles" || canFamily }),
}));

const { default: FamilyPage } = await import("./FamilyPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const profile = (over: Record<string, unknown> = {}) => ({
  id: 5,
  label: "Мама",
  status: "active",
  suspend_reason: null,
  expired: false,
  expire_at: "2026-10-26T12:00:00+00:00",
  url: "https://sub.example/mama",
  device_limit: 2,
  devices: 1,
  traffic_limit_bytes: 100 * 1024 ** 3,
  traffic_used_bytes: 1024 ** 3,
  created_at: "2026-09-20T12:00:00+00:00",
  ...over,
});

const answer = (over: Partial<FamilyResponse> = {}): FamilyResponse => ({
  enabled: true,
  available: true,
  reason: null,
  plan_name: "Семейный",
  terms: { max_profiles: 3, devices_per_profile: 2 },
  used: 1,
  profiles: [profile() as FamilyResponse["profiles"][number]],
  reset_devices: { enabled: true, cooldown_hours: 0 },
  ...over,
});

const renderPage = () =>
  render(
    <I18nProvider>
      <FamilyPage />
    </I18nProvider>,
  );

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  canFamily = true;
  for (const m of [getMock, createMock, resetMock, removeMock]) m.mockReset();
  getMock.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("FamilyPage", () => {
  it("профиль со ссылкой, устройствами и условиями тарифа", async () => {
    renderPage();
    expect(await screen.findByText("Мама")).toBeTruthy();
    expect(document.body.textContent).toContain("https://sub.example/mama");
    expect(document.body.textContent).toContain(ru("family.devices", { count: 1, limit: 2 }));
    expect(document.body.textContent).toContain(ru("family.terms", { plan: "Семейный", max: 3, devices: 2 }));
  });

  it("двойной клик «Добавить» уходит одним ключом", async () => {
    let finish: (v: unknown) => void = () => {};
    createMock.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    renderPage();
    const input = await screen.findByLabelText(ru("family.namePlaceholder"));
    fireEvent.change(input, { target: { value: "Папа" } });
    const button = screen.getByRole("button", { name: ru("family.add") });
    fireEvent.click(button);
    fireEvent.click(button);
    finish({ result: "created", profile_id: 6 });
    await waitFor(() => expect(document.body.textContent).toContain(ru("family.created", { name: "Папа" })));
    expect(createMock).toHaveBeenCalledTimes(1);
    const body = createMock.mock.calls[0]![0] as { request_id: string; label: string };
    expect(body.label).toBe("Папа");
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("повтор после обрыва связи — тот же ключ", async () => {
    createMock.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ result: "created", profile_id: 7 });
    renderPage();
    const input = await screen.findByLabelText(ru("family.namePlaceholder"));
    fireEvent.change(input, { target: { value: "Сын" } });
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(document.body.textContent).toContain(ru("family.errGeneric")));
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    const [first, second] = createMock.mock.calls.map((c) => (c[0] as { request_id: string }).request_id);
    expect(first).toBe(second);
  });

  it("сменили имя после обрыва — это новая просьба и новый ключ", async () => {
    createMock.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ result: "created", profile_id: 7 });
    renderPage();
    const input = await screen.findByLabelText(ru("family.namePlaceholder"));
    fireEvent.change(input, { target: { value: "Сын" } });
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(document.body.textContent).toContain(ru("family.errGeneric")));
    fireEvent.change(input, { target: { value: "Дочь" } });
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    const [first, second] = createMock.mock.calls.map((c) => c[0] as { request_id: string; label: string });
    expect(second!.label).toBe("Дочь");
    expect(first!.request_id).not.toBe(second!.request_id);
  });

  it("семья занята — «повторите через минуту», повтор тем же ключом", async () => {
    createMock.mockResolvedValueOnce({ result: "busy" }).mockResolvedValueOnce({ result: "created", profile_id: 8 });
    renderPage();
    const input = await screen.findByLabelText(ru("family.namePlaceholder"));
    fireEvent.change(input, { target: { value: "Папа" } });
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(document.body.textContent).toContain(ru("family.errBusy")));
    fireEvent.click(screen.getByRole("button", { name: ru("family.add") }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(2));
    const [first, second] = createMock.mock.calls.map((c) => (c[0] as { request_id: string }).request_id);
    expect(first).toBe(second);
  });

  it("правило замен за период видно до того, как в него упёрлись", async () => {
    getMock.mockResolvedValue(answer({ created_in_period: 2, period_limit: 4 }));
    renderPage();
    await screen.findByText("Мама");
    expect(document.body.textContent).toContain(ru("family.periodHint", { limit: 4, created: 2 }));
  });

  it("лимит за период — причина словами", async () => {
    getMock.mockResolvedValue(answer({ available: false, reason: "period_limit" }));
    renderPage();
    await screen.findByText("Мама");
    expect(document.body.textContent).toContain(
      ru("family.cantAdd", { reason: ru("family.reason.period_limit") }),
    );
  });

  it("удаление — только после подтверждения", async () => {
    removeMock.mockResolvedValue({ result: "deleted" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: ru("family.delete") }));
    expect(removeMock).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(ru("family.deleteConfirm", { name: "Мама" }));
    fireEvent.click(screen.getByRole("button", { name: ru("family.deleteYes") }));
    await waitFor(() => expect(removeMock).toHaveBeenCalledWith(5));
  });

  it("приостановленный профиль — без ссылки и сброса, с причиной", async () => {
    getMock.mockResolvedValue(
      answer({
        profiles: [profile({ status: "suspended", suspend_reason: "plan", url: null }) as FamilyResponse["profiles"][number]],
      }),
    );
    renderPage();
    await screen.findByText("Мама");
    expect(document.body.textContent).toContain(ru("family.suspended", { reason: ru("family.suspend.plan") }));
    expect(screen.queryByRole("button", { name: ru("family.resetDevices") })).toBeNull();
    expect(document.body.textContent).not.toContain("https://");
  });

  it("нельзя добавить — причина словами, формы нет", async () => {
    getMock.mockResolvedValue(answer({ available: false, reason: "max_reached" }));
    renderPage();
    await screen.findByText("Мама");
    expect(document.body.textContent).toContain(
      ru("family.cantAdd", { reason: ru("family.reason.max_reached") }),
    );
    expect(screen.queryByRole("button", { name: ru("family.add") })).toBeNull();
  });

  it("функцию выключили, профили остались — видны и удаляются, новых не заводим", async () => {
    getMock.mockResolvedValue(
      answer({ enabled: false, available: false, reason: "disabled", period_limit: 3, created_in_period: 1 }),
    );
    renderPage();
    expect(await screen.findByText("Мама")).toBeTruthy();
    expect(document.body.textContent).toContain(ru("family.reason.disabled"));
    expect(document.body.textContent).not.toContain(ru("family.addTitle"));
    expect(document.body.textContent).not.toContain(ru("family.reason.not_active"));
    expect(document.body.textContent).not.toContain(ru("family.periodHint", { limit: 3, created: 1 }));
    expect(screen.getByRole("button", { name: ru("family.delete") })).toBeTruthy();
    expect(document.body.textContent).toContain("https://sub.example/mama");
  });

  it("бот не умеет семью — страница говорит, что раздела нет, и не ходит в API", async () => {
    canFamily = false;
    renderPage();
    expect(await screen.findByText(ru("family.unavailable"))).toBeTruthy();
    expect(getMock).not.toHaveBeenCalled();
  });
});

describe("familyVisible: пункт меню", () => {
  it("виден при включённой функции и семейном тарифе или при уже заведённых профилях", () => {
    expect(familyVisible(answer())).toBe(true);
    expect(familyVisible(answer({ profiles: [] }))).toBe(true);
    expect(familyVisible(answer({ profiles: [], terms: null }))).toBe(false);
    expect(familyVisible({ enabled: false, available: false, profiles: [] })).toBe(false);
    expect(familyVisible(null)).toBe(false);
  });
});
