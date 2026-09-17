import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { detectInitialLang } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { Appearance } from "@/api/appearance";
import type { FeatureKey } from "@/lib/features";
import type { DevicesResponse, SubscriptionInfoResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { device, devicesOf, iphoneTwice, offers, plan, sub } from "@/test/offersFixtures";

// Оформление и возможности бэкенда задаёт тест: именно от них зависит, пойдёт ли
// блок в сеть. `can` — НАСТОЯЩИЙ canFeature, а не его копия: копия прежней семантики
// («нет ключа = умеет») молча разошлась бы с кодом, когда блок стал зависеть от бота.
// По умолчанию под кабинетом бот, который блок умеет (токен device_upsell).
const look = (over: Partial<Appearance> = {}) =>
  ({ brand_name: "X", bot_capabilities: ["device_upsell"], ...over }) as Appearance;
let appearance: Appearance | null = look();
vi.mock("@/contexts/BrandingContext", async () => {
  const { canFeature } = await import("@/lib/features");
  return {
    useBranding: () => ({
      appearance,
      can: (key: FeatureKey) => canFeature(appearance, key),
    }),
  };
});

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

  it("бот без токена device_upsell (1.3.8, обновили только кабинет) — не спрашиваем, блока нет", async () => {
    appearance = look({ bot_capabilities: undefined });
    const { container } = view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");

    appearance = look({ bot_capabilities: ["bulk_jobs"] });
    cleanup();
    view("devices", soloSub(), soloFull());
    await settle();
    expect(offersMock).not.toHaveBeenCalled();
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

describe("DeviceUpsellCard: перенос остатка по цене дня", () => {
  const carrying = (lost: number, days = 29) =>
    offers(showcase(), {
      plan_change_keeps_days: lost === 0,
      plan_change_carry_active: true,
      carry_mode: "carry",
      current_days_left: days,
      plan_change_carry: [
        { plan_code: "DUO2", duration_days: 30, currency: "RUB", mode: "carry", bonus_days: 14, lost_days: lost },
      ],
    });

  it("«Устройства»: вместо «сгорит» — сколько перенесётся", async () => {
    offersMock.mockResolvedValue(carrying(0));
    view("devices", soloSub(), soloFull());
    await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(document.body.textContent).toContain(say("deviceUpsell.carryDays", { left: 29, bonus: 14 }));
    expect(document.body.textContent).not.toContain(say("deviceUpsell.keepDays", { days: 29 }));
  });

  it("«Устройства»: часть перенести нельзя — предупреждение с числами", async () => {
    offersMock.mockResolvedValue(carrying(9));
    view("devices", soloSub(), soloFull());
    await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(document.body.textContent).toContain(say("billing.changeCarryLost", { bonus: 14, lost: 9 }));
  });

  it("Главная, последняя неделя: перенос без потерь — блок есть; потеря 9 дн. — пусто", async () => {
    offersMock.mockResolvedValue(carrying(0, 5));
    view("home", soloSub(5.5), soloFull());
    await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    cleanup();

    offersMock.mockResolvedValue(carrying(9, 5));
    const { container } = view("home", soloSub(5.5), soloFull());
    await waitFor(() => expect(offersMock).toHaveBeenCalledTimes(2));
    await settle();
    expect(container.textContent).toBe("");
  });
});
