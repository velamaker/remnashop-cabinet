import { describe, it, expect } from "vitest";
import type { Appearance } from "@/api/appearance";
import type { PlanChangeCarryEntry, SubscriptionOffersResponse } from "@/types/api";
import {
  billingHref,
  changeLoss,
  changeTerms,
  currencyOf,
  needsConfirm,
  paymentsBlocked,
  readBillingPreselect,
} from "./planChange";
import { homeAllows } from "./deviceUpsell";
import { offers, offersWithoutTerms, plan } from "@/test/offersFixtures";

const duo = plan("DUO2", 2);
const mine = plan("SOLO1", 1, { type: "RENEW" });

describe("changeLoss: что сгорит при смене тарифа", () => {
  it("смена при 29 оставшихся днях — сгорят 29", () => {
    expect(changeLoss(offers([duo]), duo)).toEqual({ kind: "days", days: 29 });
  });

  it("бессрочная подписка — сгорает «навсегда»", () => {
    expect(changeLoss(offers([duo], { current_is_unlimited: true, current_days_left: null }), duo)).toEqual({
      kind: "lifetime",
    });
  });

  it("продление, триал и нулевой остаток — терять нечего", () => {
    expect(changeLoss(offers([mine]), mine)).toBeNull();
    expect(changeLoss(offers([duo], { current_is_trial: true }), duo)).toBeNull();
    expect(changeLoss(offers([duo], { current_days_left: 0 }), duo)).toBeNull();
    expect(changeLoss(offers([plan("NEW2", 2, { type: "NEW" })]), plan("NEW2", 2, { type: "NEW" }))).toBeNull();
  });

  it("бэкенд не сообщил условия («Бедолага») или остаток переносится — не пугаем", () => {
    expect(changeLoss(offersWithoutTerms([duo]), duo)).toBeNull();
    expect(changeLoss(offers([duo], { plan_change_keeps_days: true }), duo)).toBeNull();
  });
});

describe("readBillingPreselect: ссылка только предвыбирает", () => {
  const o = offers([duo, plan("HOME3", 3)]);
  const read = (qs: string) => readBillingPreselect(new URLSearchParams(qs), o);

  it("корректные код и срок — оба", () => {
    expect(read("plan=HOME3&days=90")).toEqual({ code: "HOME3", days: 90 });
  });

  it("чужой код — ничего, даже при верном сроке", () => {
    expect(read("plan=bogus&days=30")).toEqual({ code: null, days: null });
    expect(read("days=30")).toEqual({ code: null, days: null });
  });

  it("мусорный или отсутствующий у тарифа срок — только тариф", () => {
    expect(read("plan=HOME3&days=abc")).toEqual({ code: "HOME3", days: null });
    expect(read("plan=HOME3&days=-1")).toEqual({ code: "HOME3", days: null });
    expect(read("plan=HOME3&days=45")).toEqual({ code: "HOME3", days: null });
    expect(read("plan=HOME3&days=30.5")).toEqual({ code: "HOME3", days: null });
    expect(read("plan=HOME3")).toEqual({ code: "HOME3", days: null });
  });
});

describe("billingHref и paymentsBlocked", () => {
  it("код экранируется, ссылка читается обратно", () => {
    const href = billingHref("A&B=C D", 30);
    expect(href).toBe("/billing?plan=A%26B%3DC%20D&days=30");
    const params = new URL(href, "http://x").searchParams;
    expect(params.get("plan")).toBe("A&B=C D");
    expect(params.get("days")).toBe("30");
  });

  it("оплата закрыта только в тех-работы с галкой (по умолчанию галка стоит)", () => {
    const base = { brand_name: "X" } as Appearance;
    expect(paymentsBlocked(null)).toBe(false);
    expect(paymentsBlocked(base)).toBe(false);
    expect(paymentsBlocked({ ...base, maintenance: true })).toBe(true);
    expect(paymentsBlocked({ ...base, maintenance: true, maintenance_block_payments: false })).toBe(false);
  });
});

// ── перенос остатка ──────────────────────────────────────────────────────────

const entry = (
  code: string,
  days: number,
  bonus: number,
  over: Partial<PlanChangeCarryEntry> = {},
): PlanChangeCarryEntry => ({
  plan_code: code,
  duration_days: days,
  currency: "RUB",
  mode: "carry",
  bonus_days: bonus,
  lost_days: 0,
  capped: false,
  ...over,
});

const carrying = (
  entries: PlanChangeCarryEntry[],
  over: Partial<SubscriptionOffersResponse> = {},
): SubscriptionOffersResponse =>
  offers([duo, mine], { plan_change_keeps_days: true, carry_mode: "carry", plan_change_carry: entries, ...over });

describe("changeTerms: что будет с остатком при смене", () => {
  it("условий нет («Бедолага») — null для любого срока и валюты", () => {
    const o = offersWithoutTerms([duo]);
    expect(changeTerms(o, duo, 30, "RUB")).toBeNull();
    expect(changeTerms(o, duo, 90, "USD")).toBeNull();
    expect(changeTerms(o, duo, null, null)).toBeNull();
    // Даже если остальные поля на месте: без флага кабинет перенос не обещает.
    const noFlag = carrying([entry("DUO2", 30, 14)]);
    delete noFlag.plan_change_keeps_days;
    expect(changeTerms(noFlag, duo, 30, "RUB")).toBeNull();
  });

  it("перенос выключен — прежнее «сгорит» и «навсегда»", () => {
    expect(changeTerms(offers([duo]), duo, 30, "RUB")).toEqual({ kind: "days", days: 29 });
    expect(
      changeTerms(offers([duo], { current_is_unlimited: true, current_days_left: null }), duo, 30, "RUB"),
    ).toEqual({ kind: "lifetime" });
  });

  it("перенос: запись по тарифу, сроку и валюте — без подтверждения", () => {
    const t = changeTerms(carrying([entry("DUO2", 30, 14)]), duo, 30, "RUB");
    expect(t).toEqual({ kind: "carry", left: 29, bonus: 14, lost: 0 });
    expect(needsConfirm(t)).toBe(false);
  });

  it("другой срок — своя запись; валюта без записи — честное «сгорит» с подтверждением", () => {
    const o = carrying([entry("DUO2", 30, 14), entry("DUO2", 90, 45)]);
    expect(changeTerms(o, duo, 90, "RUB")).toEqual({ kind: "carry", left: 29, bonus: 45, lost: 0 });
    const usd = changeTerms(o, duo, 30, "USD");
    expect(usd).toEqual({ kind: "days", days: 29 });
    expect(needsConfirm(usd)).toBe(true);
  });

  it("часть перенести нельзя — подтверждение; бонус 0 без потерь — без подтверждения", () => {
    const lost = changeTerms(carrying([entry("DUO2", 30, 11, { lost_days: 3 })]), duo, 30, "RUB");
    expect(lost).toEqual({ kind: "carry", left: 29, bonus: 11, lost: 3 });
    expect(needsConfirm(lost)).toBe(true);
    const small = changeTerms(carrying([entry("DUO2", 30, 0)]), duo, 30, "RUB");
    expect(small).toEqual({ kind: "carry", left: 29, bonus: 0, lost: 0 });
    expect(needsConfirm(small)).toBe(false);
  });

  it("резерв и «нечего переносить» — молчим; возврат — «сгорит»; бессрочная; триал; продление", () => {
    const e = [entry("DUO2", 30, 14)];
    expect(changeTerms(carrying(e, { carry_mode: "reserve", current_days_left: 0 }), duo, 30, "RUB")).toBeNull();
    expect(changeTerms(carrying(e, { carry_mode: "none" }), duo, 30, "RUB")).toBeNull();
    expect(changeTerms(carrying([], { carry_mode: "refund" }), duo, 30, "RUB")).toEqual({ kind: "days", days: 29 });
    expect(
      changeTerms(carrying([], { carry_mode: "lifetime", current_is_unlimited: true, current_days_left: null }), duo, 30, "RUB"),
    ).toEqual({ kind: "lifetime" });
    expect(changeTerms(carrying(e, { current_is_trial: true }), duo, 30, "RUB")).toBeNull();
    expect(changeTerms(carrying(e), mine, 30, "RUB")).toBeNull();
    expect(needsConfirm(null)).toBe(false);
    expect(needsConfirm({ kind: "lifetime" })).toBe(true);
  });

  it("срока нет у тарифа (переключатель — объединение сроков) — null, а не «сгорит»", () => {
    const short = plan("DUO2", 2, { prices: { 30: "200", 60: "380" } });
    expect(changeTerms(carrying([entry("DUO2", 30, 14)]), short, 90, "RUB")).toBeNull();
    expect(changeTerms(carrying([entry("DUO2", 30, 14)]), short, null, "RUB")).toBeNull();
  });

  it("тот же тариф — 1:1; нет цены срока — «сгорит»; новый бессрочный — молчим", () => {
    const same = changeTerms(carrying([entry("DUO2", 30, 29, { mode: "same_plan" })]), duo, 30, "RUB");
    expect(same).toEqual({ kind: "carry", left: 29, bonus: 29, lost: 0, samePlan: true });
    expect(needsConfirm(same)).toBe(false);
    expect(changeTerms(carrying([entry("DUO2", 30, 0, { mode: "unpriced", lost_days: 29 })]), duo, 30, "RUB")).toEqual({
      kind: "days",
      days: 29,
    });
    expect(changeTerms(carrying([entry("DUO2", 30, 0, { mode: "none" })]), duo, 30, "RUB")).toBeNull();
  });

  it("Главная: перенос без потерь — можно звать; потеря больше недели — нет", () => {
    expect(homeAllows({ kind: "carry", left: 5, bonus: 3, lost: 0 })).toBe(true);
    expect(homeAllows({ kind: "carry", left: 29, bonus: 3, lost: 8 })).toBe(false);
    expect(homeAllows({ kind: "carry", left: 29, bonus: 3, lost: 7 })).toBe(true);
  });

  it("currencyOf — валюта выбранного шлюза", () => {
    const o = offers([duo]);
    expect(currencyOf(o, "YOOKASSA")).toBe("RUB");
    expect(currencyOf(o, "UNKNOWN")).toBeNull();
    expect(currencyOf(o, null)).toBeNull();
  });
});
