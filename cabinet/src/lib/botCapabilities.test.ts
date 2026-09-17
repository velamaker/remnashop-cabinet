import { describe, expect, it } from "vitest";

import type { Appearance } from "@/api/appearance";
import type { PlanChangeCarryEntry } from "@/types/api";
import { offers, plan } from "@/test/offersFixtures";
import {
  BOT_CAPABILITIES,
  FEATURE_NEEDS_BOT,
  PAGE_NEEDS_BOT,
  botHas,
  missingBotCaps,
  pageBotCap,
  pageBotReady,
  type BotCap,
} from "./botCapabilities";
import { changeTerms } from "./planChange";

const base = { brand_name: "X" } as Appearance;
const ALL = Object.keys(BOT_CAPABILITIES) as BotCap[];

describe("botHas", () => {
  it("оформления нет — новое не показываем", () => {
    expect(botHas(null, "bulk_jobs")).toBe(false);
    expect(botHas(undefined, "bulk_jobs")).toBe(false);
  });

  it("бот без поля (1.3.8) — ни одного токена", () => {
    for (const cap of ALL) expect(botHas(base, cap)).toBe(false);
  });

  it("по списку бота", () => {
    const a = { ...base, bot_capabilities: ["bulk_jobs"] } as Appearance;
    expect(botHas(a, "bulk_jobs")).toBe(true);
    expect(botHas(a, "renewal_discount")).toBe(false);
  });

  it("чужой бэкенд (features) решает сам — даже без списка и даже со списком без токена", () => {
    expect(botHas({ ...base, features: {} }, "renewal_discount")).toBe(true);
    const both = { ...base, features: { push: false }, bot_capabilities: [] } as Appearance;
    expect(botHas(both, "renewal_discount")).toBe(true);
  });

  it("мусор вместо списка или features: null — как бот без поля", () => {
    expect(botHas({ ...base, bot_capabilities: "bulk_jobs" as unknown as string[] }, "bulk_jobs")).toBe(false);
    expect(botHas({ ...base, features: null as unknown as Record<string, boolean> }, "bulk_jobs")).toBe(false);
  });
});

describe("missingBotCaps", () => {
  it("старый бот — весь манифест; новый — пусто; чужой бэкенд и неизвестность — пусто", () => {
    expect(missingBotCaps(base)).toEqual(ALL);
    expect(missingBotCaps({ ...base, bot_capabilities: [...ALL] })).toEqual([]);
    expect(missingBotCaps({ ...base, features: {} })).toEqual([]);
    expect(missingBotCaps(null)).toEqual([]);
  });

  it("бот без одного токена — ровно он", () => {
    const a = { ...base, bot_capabilities: ALL.filter((c) => c !== "ad_link_url") } as Appearance;
    expect(missingBotCaps(a)).toEqual(["ad_link_url"]);
  });
});

describe("страницы админки", () => {
  it("корень и вложенные адреса спрятанной страницы; соседи с похожим началом — нет", () => {
    expect(pageBotCap("/admin/renewal-discount")).toBe("renewal_discount");
    expect(pageBotCap("/admin/renewal-discount/stats")).toBe("renewal_discount");
    expect(pageBotCap("/admin/renewal-discount-old")).toBeNull();
    expect(pageBotCap("/admin/users")).toBeNull();
  });

  it("старый бот — страницы нет; новый и чужой бэкенд — есть; прочие страницы всегда есть", () => {
    const newBot = { ...base, bot_capabilities: ["renewal_discount"] } as Appearance;
    expect(pageBotReady(base, "/admin/renewal-discount")).toBe(false);
    expect(pageBotReady(newBot, "/admin/renewal-discount")).toBe(true);
    expect(pageBotReady({ ...base, features: {} }, "/admin/renewal-discount")).toBe(true);
    expect(pageBotReady(base, "/admin/users")).toBe(true);
    expect(pageBotReady(null, "/admin/users")).toBe(true);
  });

  it("таблицы ссылаются только на токены из манифеста", () => {
    for (const cap of [...Object.values(FEATURE_NEEDS_BOT), ...Object.values(PAGE_NEEDS_BOT)]) {
      expect(ALL).toContain(cap);
    }
  });
});

// update.sh читает манифест sed'ом из файла (итог «только кабинет»): одна запись на
// строке. Разъехался формат — скрипт молча перестал бы называть скрытые функции.
const RAW = import.meta.glob<string>("./botCapabilities.ts", { query: "?raw", import: "default", eager: true });

describe("формат строк манифеста для update.sh", () => {
  it("каждая запись BOT_CAPABILITIES — на своей строке ровно в разбираемом виде", () => {
    const text = Object.values(RAW)[0]!;
    expect(text.length).toBeGreaterThan(100);
    // То же выражение, что в update.sh (cabinet_caps_known), только на JS.
    const line = /^ {2}([a-z0-9_]+): \{ since: "[^"]*", label: "([^"]*)" \},$/gm;
    const parsed = Object.fromEntries([...text.matchAll(line)].map((m) => [m[1], m[2]]));
    expect(Object.keys(parsed)).toEqual(ALL);
    for (const cap of ALL) expect(parsed[cap]).toBe(BOT_CAPABILITIES[cap].label);
  });

  it("подписи без двойных кавычек и переводов строки", () => {
    for (const cap of ALL) expect(BOT_CAPABILITIES[cap].label).toMatch(/^[^"\n]+$/);
  });
});

// Справочный токен plan_change_carry: кабинет его не проверяет, потому что обещание
// переноса целиком приходит из витрины. Старый бот флага переноса не шлёт — и оплата
// предупреждает о сгорании ровно как до переноса, при любом списке возможностей.
describe("перенос остатка со старым ботом — как раньше", () => {
  const target = plan("DUO2", 2);
  const table: PlanChangeCarryEntry[] = [
    { plan_code: "DUO2", duration_days: 30, currency: "RUB", mode: "carry", bonus_days: 10, lost_days: 0 },
  ];

  it("бот без переноса: «сгорит», хотя таблица переноса есть в ответе", () => {
    const o = offers([target], { plan_change_carry: table });
    expect(changeTerms(o, target, 30, "RUB")).toEqual({ kind: "days", days: 29 });
  });

  it("бот 1.3.8 без условий смены — молчим, как кабинет 1.3.8", () => {
    const o = offers([target]);
    delete o.plan_change_keeps_days;
    expect(changeTerms(o, target, 30, "RUB")).toBeNull();
  });

  it("новый бот с переносом — перенос", () => {
    const o = offers([target], { plan_change_carry_active: true, carry_mode: "carry", plan_change_carry: table });
    expect(changeTerms(o, target, 30, "RUB")).toMatchObject({ kind: "carry", bonus: 10, lost: 0 });
  });
});
