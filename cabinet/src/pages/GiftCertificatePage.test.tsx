import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import { ApiError } from "@/types/api";

/**
 * Подарочный сертификат по ссылке.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Ссылку пересылают, и открыть её может кто угодно — в том
 * числе превью мессенджера и антивирус. Поэтому:
 *   * открытие ничего не активирует — только кнопка;
 *   * у каждого состояния свой честный ответ: готов, уже забран, удалён из
 *     админки (оплачен — значит в поддержку), нет такого, сбой;
 *   * «в кабинете» ведёт на поле промокода с кодом, а гостю ещё и предлагает
 *     зарегистрироваться, не теряя код по дороге.
 */

const certificate = vi.fn();
vi.mock("@/api/gift", () => ({ giftApi: { certificate: (code: string) => certificate(code) } }));

let user: unknown = null;
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user }) }));
// Шапка страницы тянет оформление и переключатели — к сертификату отношения не имеют.
vi.mock("@/components/BrandLogo", () => ({ BrandLogo: () => null }));
vi.mock("@/components/BrandWordmark", () => ({ BrandWordmark: () => null }));
vi.mock("@/components/LanguageSwitcher", () => ({ LanguageSwitcher: () => null }));
vi.mock("@/components/ui/ThemeSwitcher", () => ({ ThemeSwitcher: () => null }));

const { GiftCertificatePanel } = await import("./GiftCertificatePage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const CODE = "GIFT-" + "A".repeat(32);

function cert(over: Record<string, unknown> = {}) {
  return {
    code: CODE,
    plan_name: "DUO · 2 📱",
    days: 30,
    state: "ready",
    bot_url: `https://t.me/test_bot?start=promo_${CODE}`,
    ...over,
  };
}

function open() {
  render(
    <MemoryRouter>
      <I18nProvider>
        <GiftCertificatePanel code={CODE} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  user = null;
  certificate.mockReset();
});
afterEach(() => cleanup());

describe("сертификат: что видит получатель", () => {
  it("готовый подарок: что подарено и обе кнопки активации", async () => {
    certificate.mockResolvedValue(cert());
    open();
    await screen.findByText("DUO · 2 📱");
    expect(screen.getByText(ru("gift.cert.days", { n: 30 }))).toBeTruthy();

    const tg = screen.getByText(ru("gift.cert.inTelegram")).closest("a")!;
    expect(tg.getAttribute("href")).toBe(`https://t.me/test_bot?start=promo_${CODE}`);

    const cab = screen.getByText(ru("gift.cert.inCabinet")).closest("a")!;
    // /billing, а не /subscription: у человека без подписки там нет поля промокода.
    expect(cab.getAttribute("href")).toBe(`/billing?promo=${CODE}`);
  });

  it("открытие ничего не активирует — запрос только на чтение", async () => {
    certificate.mockResolvedValue(cert());
    open();
    await screen.findByText("DUO · 2 📱");
    // Единственный вызов — чтение сертификата; активации на открытии нет вовсе.
    expect(certificate).toHaveBeenCalledTimes(1);
    expect(certificate).toHaveBeenCalledWith(CODE);
  });

  it("гостю — регистрация, и код не теряется по дороге", async () => {
    certificate.mockResolvedValue(cert());
    open();
    const reg = (await screen.findByText(ru("gift.cert.noAccount"))).closest("a")!;
    expect(reg.getAttribute("href")).toBe(
      `/register?next=${encodeURIComponent(`/billing?promo=${CODE}`)}`,
    );
  });

  it("вошедшему регистрацию не предлагаем", async () => {
    user = { id: 1 };
    certificate.mockResolvedValue(cert());
    open();
    await screen.findByText("DUO · 2 📱");
    expect(screen.queryByText(ru("gift.cert.noAccount"))).toBeNull();
  });

  it("бот сейчас не ответил — остаётся кабинет, и он становится главной кнопкой", async () => {
    certificate.mockResolvedValue(cert({ bot_url: null }));
    open();
    await screen.findByText("DUO · 2 📱");
    expect(screen.queryByText(ru("gift.cert.inTelegram"))).toBeNull();
    expect(screen.getByText(ru("gift.cert.inCabinet")).closest("a")!.className).toContain(
      "btn-gradient",
    );
  });
});

describe("сертификат: честные состояния", () => {
  it("уже забран — говорим это и кнопок активации нет", async () => {
    certificate.mockResolvedValue(cert({ state: "activated", bot_url: null }));
    open();
    await screen.findByText(ru("gift.cert.activated"));
    expect(screen.queryByText(ru("gift.cert.inCabinet"))).toBeNull();
  });

  it("удалён из админки — оплачен, поэтому в поддержку, а не «нет такого»", async () => {
    certificate.mockResolvedValue(cert({ state: "void", bot_url: null }));
    open();
    await screen.findByText(ru("gift.cert.void"));
    expect(screen.queryByText(ru("gift.cert.missing"))).toBeNull();
  });

  it("такого подарка нет — просим проверить ссылку", async () => {
    certificate.mockRejectedValue(new ApiError(404, "Подарок не найден"));
    open();
    await screen.findByText(ru("gift.cert.missing"));
  });

  it("сбой — просим повторить, а не утверждаем, что подарка нет", async () => {
    certificate.mockRejectedValue(new ApiError(502, "bad gateway"));
    open();
    await waitFor(() => expect(screen.getByText(ru("gift.cert.failed"))).toBeTruthy());
    expect(screen.queryByText(ru("gift.cert.missing"))).toBeNull();
  });
});
