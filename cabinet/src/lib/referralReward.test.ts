import { describe, it, expect } from "vitest";
import { formatReferralEarned, hasReferralEarnings } from "./referralReward";

// Подписи как в кабинете, но без i18n и без toLocaleString: проверяем СКЛЕЙКУ,
// а не перевод и не разделитель разрядов (он у Node зависит от версии ICU и
// падал бы на неразрывном пробеле, ничего не говоря о самой функции).
const fmt = {
  days: (n: number) => `${n} дн.`,
  money: (n: number) => `${n} ₽`,
};

describe("заработок на рефералах", () => {
  it("рубли — обычный случай нашего бэкенда", () => {
    expect(formatReferralEarned(1250, undefined, "POINTS", fmt)).toBe("1250 ₽");
  });

  it("EXTRA_DAYS: дни лежат в earned, отдельного поля нет", () => {
    // Наш бэкенд. Если бы функция полезла в earned_days, человек увидел бы «0 дн.»
    // вместо начисленных 14.
    expect(formatReferralEarned(14, undefined, "EXTRA_DAYS", fmt)).toBe("14 дн.");
  });

  it("только дни — «дневная» программа «Бедолаги» больше не показывает 0 ₽", () => {
    // Ровно та поломка, ради которой всё это: у такого начисления
    // amount_kopeks == 0, и до правки на экране стояло «0 ₽» при работающих выплатах.
    expect(formatReferralEarned(0, 21, "POINTS", fmt)).toBe("21 дн.");
  });

  it("и деньги, и дни — показываем обе награды, а не одну", () => {
    expect(formatReferralEarned(900, 7, "POINTS", fmt)).toBe("900 ₽ + 7 дн.");
  });

  it("EXTRA_DAYS игнорирует earned_days: складывать разные величины нельзя", () => {
    expect(formatReferralEarned(14, 3, "EXTRA_DAYS", fmt)).toBe("14 дн.");
  });

  it("мусор в earned_days не ломает строку", () => {
    expect(formatReferralEarned(500, -5, "POINTS", fmt)).toBe("500 ₽");
    expect(formatReferralEarned(500, 2.7, "POINTS", fmt)).toBe("500 ₽ + 2 дн.");
  });

  it("карточку показываем, если есть хоть что-то из двух", () => {
    expect(hasReferralEarnings(0, 0)).toBe(false);
    expect(hasReferralEarnings(0, undefined)).toBe(false);
    expect(hasReferralEarnings(0, 5)).toBe(true);
    expect(hasReferralEarnings(100, 0)).toBe(true);
  });
});
