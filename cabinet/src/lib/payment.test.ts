import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { openPayment, onReturnFromPayment } from "./payment";

/**
 * 19.09 владелец пожаловался: в мини-аппе после «Оплатить» перекидывает на ЮMoney,
 * и стрелка «назад» больше не работает. Причина — `location.href` уводил сам WebView,
 * и кабинета, куда возвращаться, просто не оставалось.
 *
 * Тест держит оба поведения: в мини-аппе счёт уходит наружу (кабинет жив), в
 * обычном браузере — привычный переход на той же вкладке.
 */

const PAY_URL = "https://yoomoney.example/checkout/abc";

let assigned: string | null = null;

beforeEach(() => {
  assigned = null;
  // location.href в jsdom не навигирует, но и не даёт себя подменить обычным
  // присваиванием — перехватываем через defineProperty.
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      get href() {
        return "https://cabinet.example/billing";
      },
      set href(value: string) {
        assigned = value;
      },
      origin: "https://cabinet.example",
    },
  });
});

afterEach(() => {
  delete (window as unknown as { Telegram?: unknown }).Telegram;
  vi.restoreAllMocks();
});

function miniApp(openLink = vi.fn()) {
  (window as unknown as { Telegram?: unknown }).Telegram = {
    WebApp: { initData: "user=1", openLink },
  };
  return openLink;
}

describe("переход к оплате", () => {
  it("обычный браузер: уходим на счёт той же вкладкой", () => {
    expect(openPayment(PAY_URL)).toBe(false);
    expect(assigned).toBe(PAY_URL);
  });

  it("мини-апп: счёт открывается наружу, кабинет остаётся на месте", () => {
    const openLink = miniApp();
    expect(openPayment(PAY_URL)).toBe(true);
    expect(openLink).toHaveBeenCalledWith(PAY_URL, { try_instant_view: false });
    expect(assigned).toBeNull();
  });

  it("недопустимая ссылка не оставляет человека с мёртвой кнопкой", () => {
    const openLink = miniApp();
    // javascript: наружу не отдаём (guard в openExternalLink), но и молчать нельзя.
    expect(openPayment("javascript:alert(1)")).toBe(false);
    expect(openLink).not.toHaveBeenCalled();
    expect(assigned).toBe("javascript:alert(1)");
  });
});

describe("возвращение из окна оплаты", () => {
  it("зовёт обработчик, когда вкладка снова видима, и снимает слушателей", () => {
    const onReturn = vi.fn();
    const stop = onReturnFromPayment(onReturn);

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onReturn).not.toHaveBeenCalled();

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onReturn).toHaveBeenCalledTimes(1);

    stop();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(onReturn).toHaveBeenCalledTimes(1);
  });
});
