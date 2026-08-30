import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { captureReferralCode, readReferralCode, clearReferralCode } from "./referralRef";

describe("реф-код из ссылки приглашения", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("запоминает код из адреса и отдаёт его после входа", () => {
    captureReferralCode("?ref=OPxPiV");
    expect(readReferralCode()).toBe("OPxPiV");
  });

  it("не трогает уже запомненный код, если в адресе его нет", () => {
    captureReferralCode("?ref=OPxPiV");
    // Переход на страницу входа — адрес уже без ref, но код должен пережить это:
    // именно здесь ломалось приглашение через Telegram (кнопка входа на другой странице).
    captureReferralCode("?utm_source=tg");
    expect(readReferralCode()).toBe("OPxPiV");
  });

  it("игнорирует мусор вместо кода", () => {
    captureReferralCode("?ref=" + encodeURIComponent("<script>alert(1)</script>"));
    expect(readReferralCode()).toBeNull();
  });

  it("игнорирует слишком длинное значение", () => {
    captureReferralCode("?ref=" + "A".repeat(65));
    expect(readReferralCode()).toBeNull();
  });

  it("забывает код после использования", () => {
    captureReferralCode("?ref=ABC123");
    clearReferralCode();
    expect(readReferralCode()).toBeNull();
  });

  it("переживает недоступное хранилище и не роняет вход", () => {
    // Приватное окно и «блокировать данные сайтов» заставляют localStorage БРОСАТЬ,
    // а не возвращать null. Без try/catch на этом падала бы вся страница входа.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(() => captureReferralCode("?ref=ABC123")).not.toThrow();
    expect(readReferralCode()).toBeNull();
    expect(() => clearReferralCode()).not.toThrow();
  });
});
