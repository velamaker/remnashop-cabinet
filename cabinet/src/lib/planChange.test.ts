import { describe, it, expect } from "vitest";
import type { Appearance } from "@/api/appearance";
import { billingHref, changeLoss, paymentsBlocked, readBillingPreselect } from "./planChange";
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
