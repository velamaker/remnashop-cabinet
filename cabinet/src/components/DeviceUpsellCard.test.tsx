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
  ({ brand_name: "X", bot_capabilities: ["device_upsell", "extra_device"], ...over }) as Appearance;
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
// Докупка — отдельный, более дешёвый запрос. По умолчанию её нет (бот не умеет или
// владелец не открыл продажи), и карточка ведёт себя ровно как раньше.
const extraMock = vi.fn();
const buyMock = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: {
    offers: () => offersMock(),
    extraDevice: () => extraMock(),
    buyExtraDevice: (body: unknown) => buyMock(body),
  },
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
  extraMock.mockReset();
  extraMock.mockResolvedValue({ enabled: false });
  buyMock.mockReset();
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


describe("DeviceUpsellCard: докупка +1 устройства", () => {
  const UNTIL = new Date(Date.now() + 20 * DAY).toISOString();
  const available = (over: Record<string, unknown> = {}) => ({
    enabled: true,
    currency_symbol: "₽",
    balance: "500",
    device_limit: 1,
    plan_device_limit: 1,
    subscription_expire_at: UNTIL,
    gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
    new: { available: true, reason: null, amount: "60", until: UNTIL, days: 20 },
    slots: [],
    ...over,
  });

  it("докупка доступна — она первая, тариф побольше уходит строкой ниже", async () => {
    extraMock.mockResolvedValue(available());
    view("devices", soloSub(), soloFull());
    await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) });
    // Тариф остаётся доступным, но уже как «или тариф побольше», а не как главная кнопка.
    await waitFor(() => expect(document.body.textContent).toContain(say("extraDevice.orPlan")));
    expect(screen.queryByRole("link", { name: new RegExp(say("deviceUpsell.cta")) })).toBeNull();
  });

  it("бот без токена extra_device — за докупкой не ходим, поведение прежнее", async () => {
    appearance = look({ bot_capabilities: ["device_upsell"] });
    view("devices", soloSub(), soloFull());
    await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(extraMock).not.toHaveBeenCalled();
  });

  it("дубли одного аппарата — карточка «освободите места», за докупкой не ходим", async () => {
    view("home", soloSub(), devicesOf(iphoneTwice(), 2));
    await settle();
    expect(extraMock).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(say("deviceUpsell.freeTitle"));
  });

  it("платить нечем (баланса мало, шлюзов нет) — докупки нет, остаётся тариф", async () => {
    extraMock.mockResolvedValue(available({ balance: "1", gateways: [] }));
    view("devices", soloSub(), soloFull());
    await screen.findByRole("link", { name: new RegExp(say("deviceUpsell.cta")) });
    expect(screen.queryByText(new RegExp(say("extraDevice.buy", { price: "60 ₽" })))).toBeNull();
  });

  it("с баланса: подтверждение, ровно один POST с request_id, повтор во время запроса не шлёт второй", async () => {
    extraMock.mockResolvedValue(available());
    let release: (v: unknown) => void = () => {};
    buyMock.mockImplementation(() => new Promise((r) => (release = r)));
    view("devices", soloSub(), soloFull());

    fireEvent.click(await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) }));
    const pay = await screen.findByRole("button", { name: say("extraDevice.payBalance") });
    fireEvent.click(pay);
    fireEvent.click(pay); // второй клик, пока запрос в полёте
    await waitFor(() => expect(buyMock).toHaveBeenCalledTimes(1));
    const body = buyMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(body.pay).toBe("balance");
    expect(body.expected_amount).toBe("60");
    expect(String(body.request_id)).toMatch(/^[0-9a-f-]{36}$/);

    await act(async () => {
      release({ result: "applied", device_limit: 2, until: UNTIL, spent: "60", balance: "440" });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(document.body.textContent).toContain("2");
  });

  it("цена изменилась — новая сумма в тексте, денег не тронули", async () => {
    extraMock.mockResolvedValue(available());
    buyMock.mockResolvedValue({
      result: "price_changed",
      quote: { enabled: true, new: { available: true, amount: "75", until: UNTIL, days: 25 } },
    });
    view("devices", soloSub(), soloFull());
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) }));
    fireEvent.click(await screen.findByRole("button", { name: say("extraDevice.payBalance") }));
    await waitFor(() =>
      expect(document.body.textContent).toContain(say("extraDevice.errPrice", { price: "75 ₽" })),
    );
  });

  it("оплата картой — уходим на страницу шлюза", async () => {
    extraMock.mockResolvedValue(available({ balance: "0" }));
    buyMock.mockResolvedValue({ result: "pending", payment_id: "p", payment_url: "https://pay.example.test/1" });
    const href = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { set href(v: string) { href(v); }, get href() { return ""; } },
    });
    view("devices", soloSub(), soloFull());
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) }));
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(say("extraDevice.payGateway", { price: "60 ₽" })) }));
    await waitFor(() => expect(href).toHaveBeenCalledWith("https://pay.example.test/1"));
  });

  it("ошибка покупки — «деньги не списаны», без падения", async () => {
    extraMock.mockResolvedValue(available());
    buyMock.mockRejectedValue(new ApiError(502, "нет"));
    view("devices", soloSub(), soloFull());
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) }));
    fireEvent.click(await screen.findByRole("button", { name: say("extraDevice.payBalance") }));
    await waitFor(() => expect(document.body.textContent).toContain(say("extraDevice.errFailed")));
  });

  it("Главная: докупку спрашиваем даже вдалеке от конца срока — терять нечего", async () => {
    extraMock.mockResolvedValue(available());
    view("home", soloSub(90), soloFull());
    await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) });
    // Витрину при этом не дёргаем: до конца срока далеко, тариф там всё равно не показали бы.
    expect(offersMock).not.toHaveBeenCalled();
  });

  it("Главная: крестик прячет блок на неделю", async () => {
    extraMock.mockResolvedValue(available());
    const { container } = view("home", soloSub(90), soloFull());
    await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) });
    fireEvent.click(screen.getByLabelText(say("common.hide")));
    await settle();
    expect(container.textContent).toBe("");
  });
});


describe("DeviceUpsellCard: покупка без crypto.randomUUID (превью по http)", () => {
  const UNTIL = new Date(Date.now() + 20 * DAY).toISOString();

  it("кнопка оплаты работает и ключ идемпотентности всё равно валидный", async () => {
    // В незащищённом контексте (http-превью, локальная сборка) randomUUID нет вовсе.
    // Раньше обработчик падал на нём молча, и кнопка оплаты была мёртвой.
    const original = Object.getOwnPropertyDescriptor(globalThis.crypto, "randomUUID");
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: undefined,
    });
    try {
      extraMock.mockResolvedValue({
        enabled: true,
        currency_symbol: "₽",
        balance: "500",
        device_limit: 1,
        plan_device_limit: 1,
        subscription_expire_at: UNTIL,
        gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
        new: { available: true, reason: null, amount: "60", until: UNTIL, days: 20 },
        slots: [],
      });
      buyMock.mockResolvedValue({ result: "applied", device_limit: 2, until: UNTIL, spent: "60" });
      view("devices", soloSub(), soloFull());
      fireEvent.click(
        await screen.findByRole("button", { name: new RegExp(say("extraDevice.buy", { price: "60 ₽" })) }),
      );
      fireEvent.click(await screen.findByRole("button", { name: say("extraDevice.payBalance") }));
      await waitFor(() => expect(buyMock).toHaveBeenCalledTimes(1));
      const body = buyMock.mock.calls[0]![0] as Record<string, unknown>;
      expect(String(body.request_id)).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    } finally {
      if (original) Object.defineProperty(globalThis.crypto, "randomUUID", original);
    }
  });
});
