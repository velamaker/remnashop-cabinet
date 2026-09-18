import { describe, it, expect } from "vitest";
import {
  DEFAULT_SHOW_FROM_PERCENT,
  changeTrafficNote,
  leftGb,
  offerView,
  payOptions,
  shouldAskOffer,
} from "./extraTraffic";
import type { ExtraTrafficResponse, SubscriptionOffersResponse } from "@/types/api";

/**
 * Показ докупки трафика. Здесь заперты ровно те решения, которые кабинет принимает
 * САМ, без бэкенда: идти ли вообще за предложением (это стоит запроса в панель),
 * рисовать ли кнопку (её нечем оплатить — не рисуем) и молчать ли про сгорание ГБ
 * при смене тарифа, когда бэкенд про них ничего не сказал.
 */

const GB = 1024 ** 3;

const answer = (over: Partial<ExtraTrafficResponse> = {}): ExtraTrafficResponse => ({
  enabled: true,
  currency_symbol: "₽",
  gb: 50,
  price: "50",
  balance: "500",
  traffic_limit_gb: 300,
  plan_traffic_limit_gb: 300,
  used_bytes: 280 * GB,
  extra_gb_active: 0,
  strategy: "MONTH_ROLLING",
  resets_at: "2026-10-07T00:10:00Z",
  show_from_percent: 70,
  gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
  offer: { available: true, reason: null, until: "2026-10-07T00:10:00Z", hours_left: 400 },
  ...over,
});

describe("порог: когда вообще спрашивать предложение", () => {
  it("безлимиту не предлагаем никогда — ни запроса, ни кнопки", () => {
    expect(shouldAskOffer({ traffic_limit: 0, used_traffic_bytes: 0 })).toBe(false);
  });

  it("ниже порога — молчим (запрос стоит похода в панель)", () => {
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 10 * GB })).toBe(false);
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 69 * GB })).toBe(false);
  });

  it("с порога и выше — спрашиваем", () => {
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 70 * GB })).toBe(true);
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 99 * GB })).toBe(true);
  });

  it("трафик кончился — спрашиваем всегда: это и есть главный случай", () => {
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 100 * GB })).toBe(true);
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 140 * GB })).toBe(true);
  });

  it("порог владельца может быть строже или мягче постоянного", () => {
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 50 * GB }, 40)).toBe(true);
    expect(shouldAskOffer({ traffic_limit: 100, used_traffic_bytes: 80 * GB }, 90)).toBe(false);
    expect(DEFAULT_SHOW_FROM_PERCENT).toBe(70);
  });

  it("подписки нет — спрашивать не у кого", () => {
    expect(shouldAskOffer(null)).toBe(false);
    expect(shouldAskOffer(undefined)).toBe(false);
  });
});

describe("предложение: рисуем только то, что можно купить", () => {
  it("продажи выключены — предложения нет и цены не знаем", () => {
    expect(offerView({ enabled: false })).toBeNull();
  });

  it("бэкенд отказал — предложения нет", () => {
    expect(offerView(answer({ offer: { available: false, reason: "reset_too_soon" } }))).toBeNull();
  });

  it("панель молчит — кнопки нет, но карточка расхода остаётся (reason приходит)", () => {
    const data = answer({ offer: { available: false, reason: "panel_unavailable" } });
    expect(offerView(data)).toBeNull();
    expect(data.offer?.reason).toBe("panel_unavailable");
  });

  it("обычный случай — объём, цена и срок", () => {
    const view = offerView(answer())!;
    expect(view.gb).toBe(50);
    expect(view.price).toBe("50");
    expect(view.until).toBe("2026-10-07T00:10:00Z");
    expect(view.endingSoon).toBe(false);
  });

  it("до обновления меньше суток — предупреждаем отдельной строкой", () => {
    const view = offerView(
      answer({ offer: { available: true, until: "2026-09-19T00:10:00Z", hours_left: 12 } }),
    )!;
    expect(view.endingSoon).toBe(true);
  });

  it("стратегия без обновления — срок пустой, а не выдуманный", () => {
    const view = offerView(
      answer({ strategy: "NO_RESET", resets_at: null, offer: { available: true, until: null } }),
    )!;
    expect(view.until).toBeNull();
  });
});

describe("чем платить", () => {
  it("хватает баланса и есть шлюз — оба способа", () => {
    expect(payOptions(answer(), "50").options).toEqual(["balance", "gateway"]);
  });

  it("баланса не хватает — говорим об этом прямо и оставляем карту", () => {
    const pay = payOptions(answer({ balance: "10" }), "50");
    expect(pay.options).toEqual(["gateway"]);
    expect(pay.balanceLow).toBe(true);
  });

  it("платить нечем — предложения нет вовсе", () => {
    const pay = payOptions(answer({ balance: "10", gateways: [] }), "50");
    expect(pay.options).toEqual([]);
    expect(pay.gateway).toBeNull();
  });
});

describe("сколько осталось", () => {
  it("считает остаток и не уходит в минус", () => {
    expect(leftGb(answer())).toBe(20);
    expect(leftGb(answer({ used_bytes: 400 * GB }))).toBe(0);
  });

  it("панель молчит — числа нет, а не ноль", () => {
    expect(leftGb(answer({ used_bytes: null }))).toBeNull();
  });
});

describe("предупреждение о сгорании при смене тарифа", () => {
  const offers = (over: Partial<SubscriptionOffersResponse>) =>
    ({ gateways: [], plans: [], ...over }) as SubscriptionOffersResponse;

  it("докуплено — говорим, сколько ГБ сгорит", () => {
    expect(changeTrafficNote(offers({ current_extra_traffic_gb: 50 }))).toEqual({ gb: 50 });
  });

  it("не докуплено — молчим", () => {
    expect(changeTrafficNote(offers({ current_extra_traffic_gb: 0 }))).toBeNull();
  });

  it("бэкенд про докупку не знает (старый бот, «Бедолага») — молчим, а не выдумываем", () => {
    expect(changeTrafficNote(offers({}))).toBeNull();
    expect(changeTrafficNote(null)).toBeNull();
  });
});
