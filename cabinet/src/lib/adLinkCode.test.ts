import { describe, expect, it } from "vitest";
import { AD_CODE_MAX, adCodeProblem, suggestAdCode } from "./adLinkCode";

describe("код рекламной ссылки", () => {
  it("предлагает код из русского названия транслитом", () => {
    expect(suggestAdCode("Сторис у блогера")).toBe("storis_u_blogera");
    expect(suggestAdCode("Пост в канале — сентябрь!")).toBe("post_v_kanale_sentyabr");
  });

  it("латиница и цифры сохраняются, мусор по краям убирается", () => {
    expect(suggestAdCode("  Instagram Story 2 ")).toBe("instagram_story_2");
  });

  it("из одних символов без букв кода не выходит", () => {
    expect(suggestAdCode("🔥🔥")).toBe("");
  });

  it("предложенный код всегда проходит проверку", () => {
    for (const name of ["Сторис у блогера", "Щука & ёж", "a".repeat(200), "Реклама №1"]) {
      const code = suggestAdCode(name);
      expect(code.length).toBeLessThanOrEqual(AD_CODE_MAX);
      expect(adCodeProblem(code)).toBeNull();
    }
  });

  it("отклоняет то, что Telegram не передаст в ссылке", () => {
    // Именно такие коды раньше сохранялись и молча не считали переходы.
    expect(adCodeProblem("сторис")).not.toBeNull();
    expect(adCodeProblem("insta story")).not.toBeNull();
    expect(adCodeProblem("promo.june")).not.toBeNull();
    expect(adCodeProblem("a".repeat(AD_CODE_MAX + 1))).not.toBeNull();
  });

  it("пропускает допустимые коды и не ругается на пустое поле", () => {
    expect(adCodeProblem("insta_story-june2")).toBeNull();
    expect(adCodeProblem("a".repeat(AD_CODE_MAX))).toBeNull();
    expect(adCodeProblem("")).toBeNull();
  });
});
