import { describe, expect, it } from "vitest";
import { serverShare } from "./serverShare";

/**
 * Карточка «Наши серверы» показывает ДОЛЮ, а не гигабайты. Причина в данных: у
 * панели раскладка по нодам и счётчик расхода пользователя расходятся в 1.5–2 раза,
 * и два несходящихся числа на одном экране владелец справедливо назвал обманом
 * («не совпадает трафик»). Тест держит арифметику долей и все вырожденные случаи —
 * пустой список, ноль трафика, единственный сервер.
 */
describe("доля сервера в трафике", () => {
  it("считает процент от суммы", () => {
    const nodes = [33, 16, 3, 0.2];
    const sum = nodes.reduce((a, b) => a + b, 0);
    expect(nodes.map((n) => serverShare(n, sum))).toEqual([63, 31, 6, 0]);
  });

  it("единственный сервер — это все 100%", () => {
    expect(serverShare(42, 42)).toBe(100);
  });

  it("нулевой и пустой расход не дают ни NaN, ни деления на ноль", () => {
    expect(serverShare(0, 0)).toBe(0);
    expect(serverShare(5, 0)).toBe(0);
    expect(serverShare(0, 10)).toBe(0);
    expect(serverShare(Number.NaN, 10)).toBe(0);
    expect(serverShare(10, Number.NaN)).toBe(0);
  });

  it("доли в сумме дают около 100% — карточку можно читать целиком", () => {
    const nodes = [512, 256, 128, 64];
    const sum = nodes.reduce((a, b) => a + b, 0);
    const total = nodes.reduce((acc, n) => acc + serverShare(n, sum), 0);
    expect(Math.abs(total - 100)).toBeLessThanOrEqual(2);
  });
});

describe("подпись доли", () => {
  it("меньше процента — это «<1%», а не «0%»", async () => {
    const { serverShareLabel } = await import("./serverShare");
    expect(serverShareLabel(0.2, 100)).toBe("<1%");
    expect(serverShareLabel(62, 100)).toBe("62%");
    expect(serverShareLabel(0, 100)).toBe("0%");
    expect(serverShareLabel(0, 0)).toBe("0%");
  });
});
