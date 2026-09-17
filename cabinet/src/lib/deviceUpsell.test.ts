import { describe, it, expect } from "vitest";
import {
  deviceLimitState,
  homeAllows,
  homePrecheck,
  pickDeviceUpgrade,
} from "./deviceUpsell";
import { device, devicesOf, iphoneTwice, offers, plan, sub } from "@/test/offersFixtures";

const DAY = 86400_000;

// Витрина как на бою: свой тариф (RENEW), три побольше, безлимит по устройствам.
const showcase = () => [
  plan("SOLO1", 1, { type: "RENEW" }),
  plan("DUO2", 2),
  plan("HOME3", 3),
  plan("UNL0", 0),
];

const current = (maxDevices: number, over: Partial<{ trafficLimit: number; durationDays: number }> = {}) => ({
  maxDevices,
  trafficLimit: 0,
  durationDays: 30,
  ...over,
});

describe("deviceLimitState: заполнен ли лимит и поможет ли уборка", () => {
  it("3 из 3, два слота — один айфон: предлагаем освободить, а не тариф", () => {
    const d = devicesOf([...iphoneTwice(), device()], 3);
    expect(deviceLimitState(sub(), d)).toEqual({ kind: "free", slots: 1 });
  });

  it("5 из 3 и один дубль: уборка упор не снимает — это full", () => {
    const d = devicesOf([...iphoneTwice(), device(), device(), device()], 3);
    expect(deviceLimitState(sub(), d)).toEqual({ kind: "full" });
  });

  it("без лимита, лимит не заполнен — ok; 4 из 3 без дублей — full", () => {
    const twelve = Array.from({ length: 12 }, () => device());
    expect(deviceLimitState(sub(), devicesOf(twelve, 0))).toEqual({ kind: "ok" });
    expect(deviceLimitState(sub(), devicesOf([device(), device()], 3))).toEqual({ kind: "ok" });
    const four = [device(), device(), device(), device()];
    expect(deviceLimitState(sub(), devicesOf(four, 3))).toEqual({ kind: "full" });
  });

  it("не активная подписка, пауза или нет данных — молчим", () => {
    const full = devicesOf([device(), device()], 2);
    for (const status of ["EXPIRED", "LIMITED", "DISABLED"]) {
      expect(deviceLimitState(sub({ status }), full)).toEqual({ kind: "ok" });
    }
    expect(deviceLimitState(sub({ frozen: true }), full)).toEqual({ kind: "ok" });
    expect(deviceLimitState(null, full)).toEqual({ kind: "ok" });
    expect(deviceLimitState(sub(), null)).toEqual({ kind: "ok" });
  });
});

describe("pickDeviceUpgrade: какой тариф предложить", () => {
  it("лимит 1 — ближайший DUO, а не самый большой", () => {
    const got = pickDeviceUpgrade(offers(showcase()), current(1));
    expect(got?.plan.public_code).toBe("DUO2");
    expect(got?.days).toBe(30);
    expect(got?.price.final_amount).toBe("200");
  });

  it("пауза и бессрочная подписка — ничего", () => {
    expect(pickDeviceUpgrade(offers(showcase(), { current_frozen: true }), current(1))).toBeNull();
    expect(pickDeviceUpgrade(offers(showcase(), { current_is_unlimited: true }), current(1))).toBeNull();
  });

  it("лимит 10 и больше нет — ничего; появился безлимит по устройствам — он", () => {
    const top = [plan("TEAM5", 5), plan("MAX10", 10)];
    expect(pickDeviceUpgrade(offers(top), current(10))).toBeNull();
    expect(pickDeviceUpgrade(offers([...top, plan("UNL0", 0)]), current(10))?.plan.public_code).toBe("UNL0");
  });

  it("трафик не урезаем: при безлимите «5 устр./400 ГБ» не предлагается", () => {
    const list = [plan("FAM5", 5, { traffic: 400 }), plan("UNL10", 10, { traffic: 0 })];
    expect(pickDeviceUpgrade(offers(list), current(4))?.plan.public_code).toBe("UNL10");
    // И наоборот: при лимите 150 ГБ тариф на 100 ГБ — шаг назад, на 200 — годится.
    const capped = [plan("LOW2", 2, { traffic: 100 }), plan("HI3", 3, { traffic: 200 })];
    expect(pickDeviceUpgrade(offers(capped), current(1, { trafficLimit: 150 }))?.plan.public_code).toBe("HI3");
  });

  it("RENEW-тариф с большим лимитом не предлагается", () => {
    const list = [plan("MINE5", 5, { type: "RENEW" })];
    expect(pickDeviceUpgrade(offers(list), current(2))).toBeNull();
  });

  it("срок: текущий, если есть у тарифа; иначе самый короткий", () => {
    const list = [plan("DUO2", 2, { prices: { 30: "200", 90: "540", 180: "1000" } })];
    expect(pickDeviceUpgrade(offers(list), current(1, { durationDays: 90 }))?.days).toBe(90);
    expect(pickDeviceUpgrade(offers(list), current(1, { durationDays: 3 }))?.days).toBe(30);
    expect(pickDeviceUpgrade(offers(list), current(1, { durationDays: 45 }))?.days).toBe(30);
  });

  it("тариф без цены на выбранный срок исключён", () => {
    const list = [plan("DUO2", 2, { prices: { 30: "" } }), plan("HOME3", 3)];
    expect(pickDeviceUpgrade(offers(list), current(1))?.plan.public_code).toBe("HOME3");
    expect(pickDeviceUpgrade(offers([plan("DUO2", 2, { prices: { 30: "" } })]), current(1))).toBeNull();
  });

  it("при равных устройствах — дешевле, дальше порядок витрины", () => {
    const list = [
      plan("A2", 2, { prices: { 30: "300" } }),
      plan("B2", 2, { prices: { 30: "250" } }),
      plan("C2", 2, { prices: { 30: "250" } }),
    ];
    expect(pickDeviceUpgrade(offers(list), current(1))?.plan.public_code).toBe("B2");
  });

  it("без шлюзов купить нечем — ничего", () => {
    expect(pickDeviceUpgrade(offers(showcase(), { gateways: [] }), current(1))).toBeNull();
  });

  it("бэкенд не сообщил условия смены (нет plan_change_keeps_days) — ничего", () => {
    const o = offers(showcase());
    delete o.plan_change_keeps_days;
    expect(pickDeviceUpgrade(o, current(1))).toBeNull();
  });
});

describe("Главная: только когда смена почти ничего не сжигает", () => {
  it("homePrecheck: 29 дней — нет, 7 — да, триал — да", () => {
    const in29 = new Date(Date.now() + 29.5 * DAY).toISOString();
    const in7 = new Date(Date.now() + 7.5 * DAY).toISOString();
    expect(homePrecheck(sub({ expire_at: in29 }))).toBe(false);
    expect(homePrecheck(sub({ expire_at: in7 }))).toBe(true);
    expect(homePrecheck(sub({ expire_at: in29, is_trial: true }))).toBe(true);
  });

  it("homeAllows: 29 — нет, 7 — да, потери нет — да, бессрочная — нет", () => {
    expect(homeAllows({ kind: "days", days: 29 })).toBe(false);
    expect(homeAllows({ kind: "days", days: 7 })).toBe(true);
    expect(homeAllows(null)).toBe(true);
    expect(homeAllows({ kind: "lifetime" })).toBe(false);
  });
});
