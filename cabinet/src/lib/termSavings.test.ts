import { describe, expect, it } from "vitest";
import type { PlanOfferResponse } from "@/types/api";
import { defaultTermDays, planTermSavings, termSavings } from "./termSavings";

// Цены — боевые после «лестницы сроков» 26.09: выгода 25 / 35 / 40 % против помесячной.
function plan(
  code: string,
  prices: Record<number, string>,
  gw = "YOOMONEY",
  type: "NEW" | "CHANGE" | "RENEW" = "NEW",
): PlanOfferResponse {
  return {
    id: 1,
    public_code: code,
    name: code,
    description: null,
    traffic_limit: 0,
    device_limit: 1,
    type: "BOTH",
    recommended_purchase_type: type,
    durations: Object.entries(prices).map(([days, amount]) => ({
      days: Number(days),
      prices: [
        {
          gateway_type: gw,
          currency: "RUB",
          currency_symbol: "₽",
          original_amount: amount,
          discount_percent: 0,
          final_amount: amount,
          is_free: false,
        },
      ],
    })),
  } as unknown as PlanOfferResponse;
}

const SOLO = plan("solo", { 30: "129", 60: "239", 90: "289", 180: "499", 365: "939" });
const HOME = plan("home", { 30: "339", 90: "759", 180: "1319", 365: "2469" });
const YM = "YOOMONEY" as never;

describe("выгода срока", () => {
  it("боевые цены SOLO — 25 / 35 / 40 %", () => {
    expect(planTermSavings(SOLO, 90, YM)).toBe(25);
    expect(planTermSavings(SOLO, 180, YM)).toBe(35);
    expect(planTermSavings(SOLO, 365, YM)).toBe(40);
  });

  it("месяц и мелкая выгода не показываются", () => {
    expect(planTermSavings(SOLO, 30, YM)).toBeNull();
    expect(planTermSavings(SOLO, 60, YM)).toBe(7);
    expect(planTermSavings(plan("x", { 30: "100", 60: "197" }), 60, YM)).toBeNull();
  });

  it("нет месячной цены или шлюза — без выгоды", () => {
    expect(planTermSavings(plan("x", { 90: "289" }), 90, YM)).toBeNull();
    expect(planTermSavings(SOLO, 90, null)).toBeNull();
    expect(planTermSavings(SOLO, 90, "TELEGRAM_STARS" as never)).toBeNull();
  });

  it("на кнопке — минимум по тарифам, чтобы не обещать лишнего", () => {
    // HOME 90 дней: 1 − 759/1017 = 25,3 %; SOLO — 25,3 %; минимум 25.
    expect(termSavings([SOLO, HOME], 90, YM)).toBe(25);
    const worse = plan("worse", { 30: "100", 90: "280" });
    expect(termSavings([SOLO, worse], 90, YM)).toBe(6);
  });
});

/**
 * ЧТО ЗАПЕРТО. Кнопка срока общая для всех карточек, и число на ней — обещание для
 * любого тарифа. Раньше тариф с выгодой ниже порога (5 %) из минимума выпадал, и
 * кнопка 60 дней обещала «−7 %» по SOLO, хотя у соседнего тарифа выгода 4 %.
 * Теперь минимум считается по сырым процентам всех тарифов, где срок продаётся, а
 * порог применяется один раз — к минимуму.
 */
describe("выгода на общей кнопке срока — настоящий минимум", () => {
  // 60 дней: 649 против 2 × 339 = 678 → 4,3 % — ниже порога.
  const SMALL = plan("small", { 30: "339", 60: "649" });

  it("тариф с выгодой ниже порога тянет минимум вниз — кнопка молчит", () => {
    expect(termSavings([SOLO], 60, YM)).toBe(7);
    expect(termSavings([SOLO, SMALL], 60, YM)).toBeNull();
    // Порядок тарифов на витрине роли не играет.
    expect(termSavings([SMALL, SOLO], 60, YM)).toBeNull();
  });

  it("тариф со сроком, но без месячной цены — сравнить не с чем, кнопка молчит", () => {
    expect(termSavings([SOLO, plan("q", { 90: "289" })], 90, YM)).toBeNull();
    expect(termSavings([plan("q", { 90: "289" }), SOLO], 90, YM)).toBeNull();
  });

  it("тариф, у которого срока нет, в минимум не идёт", () => {
    // У HOME нет 60 дней — на кнопке 60 дней честная выгода SOLO.
    expect(termSavings([SOLO, HOME], 60, YM)).toBe(7);
    // Срок не продаётся ни у кого — и выгоды нет.
    expect(termSavings([HOME], 60, YM)).toBeNull();
  });

  it("срок продаётся через другой шлюз — этот тариф в минимум не идёт", () => {
    const stars = plan("stars", { 30: "339", 60: "649" }, "TELEGRAM_STARS");
    expect(termSavings([SOLO, stars], 60, YM)).toBe(7);
    expect(termSavings([SOLO, stars], 60, "TELEGRAM_STARS" as never)).toBeNull();
  });

  it("порог — к минимуму: ровно 5 % показываем, 4 % нет", () => {
    // 90 дней: 285 против 300 → 5 %; 288 против 300 → 4 %.
    expect(termSavings([SOLO, plan("five", { 30: "100", 90: "285" })], 90, YM)).toBe(5);
    expect(termSavings([SOLO, plan("four", { 30: "100", 90: "288" })], 90, YM)).toBeNull();
  });
});

describe("срок по умолчанию", () => {
  it("90 дней, если он есть у всех тарифов витрины", () => {
    expect(defaultTermDays([SOLO, HOME])).toBe(90);
  });

  it("смешанная витрина: 90 есть не у всех — первый срок первого тарифа", () => {
    // Иначе карточка «a» открылась бы на сроке, которого у неё нет: без цены и оплаты.
    expect(defaultTermDays([plan("a", { 30: "1", 60: "2" }), SOLO])).toBe(30);
    expect(defaultTermDays([SOLO, plan("a", { 60: "2", 30: "1" })])).toBe(30);
  });

  it("смешанная витрина, но у продлеваемого тарифа 90 есть — 90 дней", () => {
    const renew = plan("mine", { 30: "129", 90: "289" }, "YOOMONEY", "RENEW");
    expect(defaultTermDays([plan("a", { 30: "1", 60: "2" }), renew])).toBe(90);
  });

  it("у продлеваемого тарифа 90 нет — первый срок, даже если 90 есть у других", () => {
    const renew = plan("mine", { 30: "1", 60: "2" }, "YOOMONEY", "RENEW");
    expect(defaultTermDays([renew, SOLO])).toBe(30);
  });

  it("иначе — первый срок первого тарифа; пустая витрина — null", () => {
    expect(defaultTermDays([plan("a", { 30: "1", 60: "2" })])).toBe(30);
    expect(defaultTermDays([])).toBeNull();
  });
});
