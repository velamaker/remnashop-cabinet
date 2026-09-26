import { describe, expect, it } from "vitest";
import type { PlanOfferResponse } from "@/types/api";
import { defaultTermDays, planTermSavings, termSavings } from "./termSavings";

// Цены — боевые после «лестницы сроков» 26.09: выгода 25 / 35 / 40 % против помесячной.
function plan(code: string, prices: Record<number, string>, gw = "YOOMONEY"): PlanOfferResponse {
  return {
    id: 1,
    public_code: code,
    name: code,
    description: null,
    traffic_limit: 0,
    device_limit: 1,
    type: "BOTH",
    recommended_purchase_type: "NEW",
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

describe("выгода срока", () => {
  it("боевые цены SOLO — 25 / 35 / 40 %", () => {
    expect(planTermSavings(SOLO, 90, "YOOMONEY" as never)).toBe(25);
    expect(planTermSavings(SOLO, 180, "YOOMONEY" as never)).toBe(35);
    expect(planTermSavings(SOLO, 365, "YOOMONEY" as never)).toBe(40);
  });

  it("месяц и мелкая выгода не показываются", () => {
    expect(planTermSavings(SOLO, 30, "YOOMONEY" as never)).toBeNull();
    expect(planTermSavings(SOLO, 60, "YOOMONEY" as never)).toBe(7);
    expect(planTermSavings(plan("x", { 30: "100", 60: "197" }), 60, "YOOMONEY" as never)).toBeNull();
  });

  it("нет месячной цены или шлюза — без выгоды", () => {
    expect(planTermSavings(plan("x", { 90: "289" }), 90, "YOOMONEY" as never)).toBeNull();
    expect(planTermSavings(SOLO, 90, null)).toBeNull();
    expect(planTermSavings(SOLO, 90, "TELEGRAM_STARS" as never)).toBeNull();
  });

  it("на кнопке — минимум по тарифам, чтобы не обещать лишнего", () => {
    // HOME 90 дней: 1 − 759/1017 = 25,3 %; SOLO — 25,3 %; минимум 25.
    expect(termSavings([SOLO, HOME], 90, "YOOMONEY" as never)).toBe(25);
    const worse = plan("worse", { 30: "100", 90: "280" });
    expect(termSavings([SOLO, worse], 90, "YOOMONEY" as never)).toBe(6);
  });
});

describe("срок по умолчанию", () => {
  it("90 дней, если он есть хоть у одного тарифа", () => {
    expect(defaultTermDays([plan("a", { 30: "1", 60: "2" }), SOLO])).toBe(90);
  });
  it("иначе — первый срок первого тарифа", () => {
    expect(defaultTermDays([plan("a", { 30: "1", 60: "2" })])).toBe(30);
    expect(defaultTermDays([])).toBeNull();
  });
});
