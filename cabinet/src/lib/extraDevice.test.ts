import { describe, expect, it } from "vitest";

import {
  changeExtraNote,
  endsBefore,
  newOffer,
  payOptions,
  renewExtraUntil,
  slotViews,
  suggestsBiggerPlan,
} from "./extraDevice";
import type { ExtraDeviceResponse, SubscriptionOffersResponse } from "@/types/api";

// Даты и суммы синтетические.
const UNTIL = "2026-10-08T12:00:00+00:00";
const SLOT_END = "2026-09-30T08:00:00+00:00";

function data(over: Partial<ExtraDeviceResponse> = {}): ExtraDeviceResponse {
  return {
    enabled: true,
    currency: "RUB",
    currency_symbol: "₽",
    price_per_30d: "100",
    max_extra: 2,
    device_limit: 3,
    plan_device_limit: 2,
    extra_count: 0,
    subscription_expire_at: UNTIL,
    balance: "500",
    gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
    new: { available: true, reason: null, amount: "60", until: UNTIL, days: 20 },
    slots: [],
    ...over,
  };
}

describe("предложение докупки", () => {
  it("нет предложения без ответа, при выключенных продажах и при отказе", () => {
    expect(newOffer(null)).toBeNull();
    expect(newOffer({ enabled: false })).toBeNull();
    expect(newOffer(data({ new: { available: false, reason: "max_reached" } }))).toBeNull();
  });

  it("предложение берёт сумму, дату и символ валюты из ответа", () => {
    expect(newOffer(data())).toEqual({
      kind: "new",
      amount: "60",
      until: UNTIL,
      days: 20,
      symbol: "₽",
    });
  });

  it("докупленные места и цена продления", () => {
    const views = slotViews(
      data({
        slots: [
          { slot_id: 12, ends_at: SLOT_END, extend: { amount: "24", until: UNTIL, days: 9 } },
          { slot_id: 13, ends_at: SLOT_END, extend: null },
        ],
      }),
    );
    expect(views).toHaveLength(2);
    expect(views[0]!.extend).toMatchObject({ kind: "extend", slotId: 12, amount: "24" });
    expect(views[1]!.extend).toBeNull();
  });
});

describe("чем платить", () => {
  it("баланса хватает — и баланс, и карта", () => {
    const pay = payOptions(data({ balance: "500" }), "60");
    expect(pay.options).toEqual(["balance", "gateway"]);
    expect(pay.balanceLow).toBe(false);
  });

  it("баланса мало, но шлюз есть — только карта, и об этом говорим", () => {
    const pay = payOptions(data({ balance: "10" }), "60");
    expect(pay.options).toEqual(["gateway"]);
    expect(pay.balanceLow).toBe(true);
  });

  it("баланса мало и шлюзов нет — предложения нет вовсе", () => {
    // Кнопка, которой нечем оплатить, хуже отсутствия кнопки: карточка честно
    // падает на тариф побольше.
    const pay = payOptions(data({ balance: "10", gateways: [] }), "60");
    expect(pay.options).toEqual([]);
    expect(pay.balanceLow).toBe(false);
  });

  it("баланса хватает ровно — платим с баланса", () => {
    expect(payOptions(data({ balance: "60", gateways: [] }), "60").options).toEqual(["balance"]);
  });
});

describe("подписи", () => {
  it("«лимит вернётся» — только если подписка переживает место", () => {
    const slot = { slot_id: 1, ends_at: SLOT_END, extend: null };
    expect(endsBefore(data(), slot)).toBe(true);
    expect(endsBefore(data({ subscription_expire_at: SLOT_END }), slot)).toBe(false);
    expect(endsBefore(data({ subscription_expire_at: null }), slot)).toBe(false);
  });

  it("тариф побольше предлагаем, когда мест больше нельзя или место уже покупали", () => {
    expect(suggestsBiggerPlan(data({ new: { available: false, reason: "max_reached" } }))).toBe(true);
    // Решение владельца: место кончилось — второй раз не предлагаем, ведём на тариф.
    expect(suggestsBiggerPlan(data({ new: { available: false, reason: "already_used" } }))).toBe(true);
    expect(suggestsBiggerPlan(data({ new: { available: false, reason: "too_late" } }))).toBe(false);
    expect(suggestsBiggerPlan(null)).toBe(false);
  });
});

function offers(over: Partial<SubscriptionOffersResponse> = {}): SubscriptionOffersResponse {
  return {
    gateways: [],
    plans: [],
    has_current_subscription: true,
    current_subscription_status: "ACTIVE",
    ...over,
  };
}

describe("смена тарифа при докупленных местах", () => {
  it("бэкенд про места молчит — молчим и мы", () => {
    // «Бедолага» и старый бот полей не шлют: обещать перенос стоимости нельзя.
    expect(changeExtraNote(offers(), 3)).toBeNull();
    expect(changeExtraNote(null, 3)).toBeNull();
    expect(changeExtraNote(offers({ current_extra_devices: 0 }), 3)).toBeNull();
  });

  it("перенос включён и режим подходящий — стоимость уйдёт днями", () => {
    const note = changeExtraNote(
      offers({ current_extra_devices: 1, plan_change_carry_active: true, carry_mode: "carry" }),
      3,
    );
    expect(note).toEqual({ kind: "carry", count: 1, limit: 3 });
  });

  it("тот же тариф — тоже перенос: extras там уже учитываются", () => {
    const note = changeExtraNote(
      offers({ current_extra_devices: 2, plan_change_carry_active: true, carry_mode: "same_plan" }),
      4,
    );
    expect(note?.kind).toBe("carry");
  });

  it("перенос выключен или режим без пересчёта — честное «не пересчитывается»", () => {
    expect(
      changeExtraNote(offers({ current_extra_devices: 1, plan_change_carry_active: false }), 3)?.kind,
    ).toBe("lost");
    expect(
      changeExtraNote(
        offers({ current_extra_devices: 1, plan_change_carry_active: true, carry_mode: "reserve" }),
        3,
      )?.kind,
    ).toBe("lost");
  });

  it("при продлении место продолжает жить до своей даты", () => {
    expect(renewExtraUntil(offers({ current_extra_until: SLOT_END }))).toBe(SLOT_END);
    expect(renewExtraUntil(offers())).toBeNull();
  });
});
