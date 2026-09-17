import { describe, it, expect } from "vitest";
import { ruDays } from "./pluralRu";

describe("ruDays", () => {
  it.each([
    [1, "1 день"],
    [2, "2 дня"],
    [5, "5 дней"],
    [11, "11 дней"],
    [21, "21 день"],
    [22, "22 дня"],
  ])("%i → %s", (n, text) => {
    expect(ruDays(n)).toBe(text);
  });
});
