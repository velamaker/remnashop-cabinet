import { describe, expect, it } from "vitest";

import type { Appearance } from "@/api/appearance";
import { canFeature } from "./features";

const base = { brand_name: "X" } as Appearance;

describe("canFeature", () => {
  // Эти четыре случая — гарантия того, что установка с нашим ботом (он поля
  // `features` не отдаёт) ведёт себя ровно как раньше. Если тут что-то станет
  // false, кабинет у действующих пользователей начнёт прятать рабочие разделы.
  it("нет оформления — возможность доступна", () => {
    expect(canFeature(null, "gift")).toBe(true);
    expect(canFeature(undefined, "gift")).toBe(true);
  });

  it("оформление без поля features — доступна", () => {
    expect(canFeature(base, "gift")).toBe(true);
  });

  it("пустой список возможностей — доступна", () => {
    expect(canFeature({ ...base, features: {} }, "gift")).toBe(true);
  });

  it("другого ключа нет в списке — доступна", () => {
    expect(canFeature({ ...base, features: { push: false } }, "gift")).toBe(true);
  });

  it("выключена только явным false", () => {
    expect(canFeature({ ...base, features: { gift: false } }, "gift")).toBe(false);
  });

  it("true оставляет доступной", () => {
    expect(canFeature({ ...base, features: { gift: true } }, "gift")).toBe(true);
  });
});
