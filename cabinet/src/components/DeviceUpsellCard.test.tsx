import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { detectInitialLang } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { Appearance } from "@/api/appearance";
import type { DevicesResponse, SubscriptionInfoResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { device, devicesOf, iphoneTwice, offers, plan, sub } from "@/test/offersFixtures";

// Оформление и возможности бэкенда задаёт тест: именно от них зависит, пойдёт ли
// блок в сеть. `can` — как в настоящем контексте (canFeature: «нет ключа = умеет»).
const look = (over: Partial<Appearance> = {}) => ({ brand_name: "X", ...over }) as Appearance;
let appearance: Appearance | null = look();
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({
    appearance,
    can: (key: string) => appearance?.features?.[key] !== false,
  }),
}));

const offersMock = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: { offers: () => offersMock() },
}));

const { DeviceUpsellCard } = await import("./DeviceUpsellCard");

const lang = detectInitialLang();
const say = (key: string, vars?: Record<string, string | number>) => translate(key, vars, lang);
const DAY = 86400_000;

const showcase = () => [
  plan("SOLO1", 1, { type: "RENEW" }),
  plan("DUO2", 2),
  plan("HOME3", 3),
];

function view(
  variant: "home" | "devices",
  s: SubscriptionInfoResponse | null,
  d: DevicesResponse | null,
) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <DeviceUpsellCard variant={variant} subscription={s} devices={d} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

/** SOLO: одно место, и оно занято. */
const soloSub = (days = 29.5, over: Partial<SubscriptionInfoResponse> = {}) =>
  sub({ device_limit: 1, plan_name: "SOLO1", expire_at: new Date(Date.now() + days * DAY).toISOString(), ...over });
const soloFull = () => devicesOf([device()], 1);

/** Дать отработать эффектам и промисам, прежде чем утверждать «ничего не было». */
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  appearance = look();
  offersMock.mockReset();
  offersMock.mockResolvedValue(offers(showcase()));
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DeviceUpsellCard: замки «Бедолаги» и тумблеров — без сети", () => {
  it("features.device_upsell === false — витрину не спрашиваем, блока нет", async () => {
    appearance = look({ features: { device_upsell: false } });
    const { container } = view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });

  it("оформление ещё не загрузилось — не спрашиваем (первый заход без кэша)", async () => {
    appearance = null;
    const { container } = view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });

  it("тумблер device_upsell_enabled: false — не спрашиваем", async () => {
    appearance = look({ device_upsell_enabled: false });
    view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
  });

  it("тех-работы закрыли оплату — не спрашиваем", async () => {
    appearance = look({ maintenance: true });
    view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
  });

  it("лимит не заполнен — не спрашиваем", async () => {
    view("devices", soloSub(), devicesOf([], 1));
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
  });
});

describe("DeviceUpsellCard: ошибки витрины — тишина", () => {
  it.each([501, 404])("витрина ответила %i — пусто и без падения", async (code) => {
    offersMock.mockRejectedValue(new ApiError(code, "нет"));
    const { container } = view("devices", soloSub(), soloFull());
    await waitFor(() => expect(offersMock).toHaveBeenCalledTimes(1));
    await settle();
    expect(container.textContent).toBe("");
  });

  it("витрина без условий смены (старый бэкенд) — пусто", async () => {
    const o = offers(showcase());
    delete o.plan_change_keeps_days;
    offersMock.mockResolvedValue(o);
    const { container } = view("devices", soloSub(), soloFull());
    await waitFor(() => expect(offersMock).toHaveBeenCalled());
    await settle();
    expect(container.textContent).toBe("");
  });
});

describe("DeviceUpsellCard на «Устройствах»", () => {
  it("лимит заполнен — DUO, ссылка ровно на оплату с тарифом и сроком, честно про 29 дней", async () => {
    view("devices", soloSub(), soloFull());
    const link = await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(link.getAttribute("href")).toBe("/billing?plan=DUO2&days=30");
    expect(document.body.textContent).toContain(say("deviceUpsell.title"));
    expect(document.body.textContent).toContain(say("deviceUpsell.keepDays", { days: 29 }));
    expect(offersMock).toHaveBeenCalledTimes(1);
  });

  it("места заняли дубли и уборка снимает упор — молчим (там подсказка про дубли)", async () => {
    const { container } = view("devices", sub({ device_limit: 2 }), devicesOf(iphoneTwice(), 2));
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });

  it("витрина говорит «на паузе» — ничего не предлагаем", async () => {
    offersMock.mockResolvedValue(offers(showcase(), { current_frozen: true }));
    const { container } = view("devices", soloSub(), soloFull());
    await waitFor(() => expect(offersMock).toHaveBeenCalled());
    await settle();
    expect(container.textContent).toBe("");
  });
});

describe("DeviceUpsellCard на Главной", () => {
  it("до конца срока 29 дней — витрину даже не спрашиваем", async () => {
    const { container } = view("home", soloSub(29.5), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
  });

  it("последняя неделя срока — блок есть", async () => {
    offersMock.mockResolvedValue(offers(showcase(), { current_days_left: 5 }));
    view("home", soloSub(5.5), soloFull());
    const link = await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(link.getAttribute("href")).toBe("/billing?plan=DUO2&days=30");
  });

  it("срок подошёл, но бэкенд насчитал больше недели потери — всё равно молчим", async () => {
    offersMock.mockResolvedValue(offers(showcase(), { current_days_left: 20 }));
    const { container } = view("home", soloSub(5.5), soloFull());
    await waitFor(() => expect(offersMock).toHaveBeenCalled());
    await settle();
    expect(container.textContent).toBe("");
  });

  it("дубли — карточка «освободите места» со ссылкой на устройства, без витрины", async () => {
    view("home", sub({ device_limit: 2 }), devicesOf(iphoneTwice(), 2));
    const link = await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.freeCta")) });
    expect(link.getAttribute("href")).toBe("/devices");
    expect(document.body.textContent).toContain(say("deviceUpsell.freeText", { n: 1 }));
    expect(offersMock).not.toHaveBeenCalled();
  });

  it("крестик прячет блок на неделю: ключ записан, повторный рендер пуст", async () => {
    offersMock.mockResolvedValue(offers(showcase(), { current_days_left: 5 }));
    const first = view("home", soloSub(5.5), soloFull());
    fireEvent.click(await screen.findByRole("button", { name: say("common.hide") }));
    expect(first.container.textContent).toBe("");
    const until = Number(localStorage.getItem("device_upsell_hidden_until"));
    expect(until).toBeGreaterThan(Date.now() + 6 * DAY);
    cleanup();

    offersMock.mockClear();
    const again = view("home", soloSub(5.5), soloFull());
    await settle();
    expect(again.container.textContent).toBe("");
    expect(offersMock).not.toHaveBeenCalled();
  });

  it("хранилище недоступно — блок всё равно рисуется, крестик не роняет", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    offersMock.mockResolvedValue(offers(showcase(), { current_days_left: 5 }));
    view("home", soloSub(5.5), soloFull());
    const hide = await screen.findByRole("button", { name: say("common.hide") });
    fireEvent.click(hide);
    expect(screen.queryByRole("link", { name: new RegExp(say("deviceUpsell.cta")) })).toBeNull();
  });
});
