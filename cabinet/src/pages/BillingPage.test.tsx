import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { Appearance } from "@/api/appearance";
import type { SubscriptionOffersResponse } from "@/types/api";
import { GW, offers, offersWithoutTerms, plan } from "@/test/offersFixtures";

// Замок денежного пути. Смена тарифа у нас сжигает остаток срока, поэтому первый
// клик по оплате такого тарифа обязан ТОЛЬКО спросить — ни шлюз, ни списание с
// баланса до «да» не вызываются. А там, где терять нечего (продление, чужой
// бэкенд без условий смены), всё обязано работать с первого клика, как раньше.
const offersMock = vi.fn();
const purchase = vi.fn();
const extend = vi.fn();
const payWithBalance = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: {
    offers: () => offersMock(),
    purchase: (d: unknown) => purchase(d),
    extend: (d: unknown) => extend(d),
    payWithBalance: (d: unknown) => payWithBalance(d),
  },
}));
vi.mock("@/api/balance", () => ({
  balanceApi: { get: () => Promise.resolve({ balance: 100000 }) },
}));

const appearance = { brand_name: "X" } as Appearance;
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ appearance, can: () => true }),
}));
// Соседние блоки страницы ходят в свои ручки — к оплате тарифа они отношения не имеют.
vi.mock("@/components/TrialDiscountBanner", () => ({ TrialDiscountBanner: () => null }));
vi.mock("@/components/PromocodeCard", () => ({ PromocodeCard: () => null }));

const { default: BillingPage } = await import("./BillingPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

// Витрина: свой тариф (продление) и два на смену.
const showcase = () => [plan("SOLO1", 1, { type: "RENEW" }), plan("DUO2", 2), plan("HOME3", 3)];

function open(data: SubscriptionOffersResponse, url = "/billing") {
  offersMock.mockResolvedValue(data);
  return render(
    <MemoryRouter initialEntries={[url]}>
      <I18nProvider>
        <BillingPage />
      </I18nProvider>
    </MemoryRouter>,
  );
}

/** Раскрыть карточку тарифа (заголовок карточки — кнопка с его именем). */
async function expand(code: string) {
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(code) }));
}

const selectButton = () => screen.queryByRole("button", { name: ru("billing.select") });
const balanceButton = () => screen.queryByRole("button", { name: /₽\)/ });
const yesButton = () => screen.queryByRole("button", { name: ru("billing.changeConfirmYes") });

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  for (const m of [offersMock, purchase, extend, payWithBalance]) m.mockReset();
  // Без payment_url страница никуда не уходит — тесту не нужна навигация jsdom.
  purchase.mockResolvedValue({ is_free: false, payment_url: null });
  extend.mockResolvedValue({ is_free: false, payment_url: null });
  payWithBalance.mockResolvedValue({ success: true });
});
afterEach(cleanup);

describe("BillingPage: смена тарифа с потерей дней — только после подтверждения", () => {
  it("шлюз: первый клик спрашивает, «Да» платит ровно один раз", async () => {
    open(offers(showcase(), { current_days_left: 29 }));
    await expand("DUO2");
    // Предупреждение видно ещё до клика.
    expect(document.body.textContent).toContain(ru("billing.changeWarn", { days: 29 }));

    fireEvent.click(selectButton()!);
    await act(async () => {});
    expect(purchase).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(ru("billing.changeConfirm", { days: 29 }));

    fireEvent.click(yesButton()!);
    await waitFor(() => expect(purchase).toHaveBeenCalledTimes(1));
    expect(purchase).toHaveBeenCalledWith({ plan_code: "DUO2", duration_days: 30, gateway_type: GW });
    expect(extend).not.toHaveBeenCalled();
  });

  it("баланс: то же самое для списания с баланса", async () => {
    open(offers(showcase(), { current_days_left: 29 }));
    await expand("DUO2");
    fireEvent.click(balanceButton()!);
    await act(async () => {});
    expect(payWithBalance).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain(ru("billing.changeConfirm", { days: 29 }));

    fireEvent.click(yesButton()!);
    await waitFor(() => expect(payWithBalance).toHaveBeenCalledTimes(1));
    expect(payWithBalance).toHaveBeenCalledWith({ plan_code: "DUO2", duration_days: 30, gateway_type: GW });
    expect(purchase).not.toHaveBeenCalled();
  });

  it("«Отмена» закрывает подтверждение и ничего не оплачивает", async () => {
    open(offers(showcase(), { current_days_left: 29 }));
    await expand("DUO2");
    fireEvent.click(selectButton()!);
    fireEvent.click(await screen.findByRole("button", { name: ru("common.cancel") }));
    expect(yesButton()).toBeNull();
    expect(selectButton()).not.toBeNull();
    expect(purchase).not.toHaveBeenCalled();
  });

  it("сменили срок после первого клика — подтверждение сброшено, клик снова только спрашивает", async () => {
    open(offers(showcase(), { current_days_left: 29 }));
    await expand("DUO2");
    fireEvent.click(selectButton()!);
    expect(yesButton()).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: ru("billing.termDays", { d: 90 }) }));
    expect(yesButton()).toBeNull();
    fireEvent.click(selectButton()!);
    await act(async () => {});
    expect(purchase).not.toHaveBeenCalled();
    expect(yesButton()).not.toBeNull();
  });

  it("бессрочная подписка — предупреждение и подтверждение про «навсегда»", async () => {
    open(offers(showcase(), { current_is_unlimited: true, current_days_left: null }));
    await expand("DUO2");
    expect(document.body.textContent).toContain(ru("billing.changeWarnLifetime"));
    fireEvent.click(selectButton()!);
    await act(async () => {});
    expect(purchase).not.toHaveBeenCalled();
    expect(yesButton()).not.toBeNull();
  });
});

describe("BillingPage: где терять нечего — как раньше, с первого клика", () => {
  it("продление своего тарифа — extend сразу, без вопросов", async () => {
    open(offers(showcase(), { current_days_left: 29 }));
    await expand("SOLO1");
    expect(document.body.textContent).not.toContain(ru("billing.changeWarn", { days: 29 }));
    fireEvent.click(selectButton()!);
    await waitFor(() => expect(extend).toHaveBeenCalledTimes(1));
    expect(extend).toHaveBeenCalledWith({ duration_days: 30, gateway_type: GW });
    expect(yesButton()).toBeNull();
  });

  it("витрина без условий смены («Бедолага») — purchase с первого клика", async () => {
    open(offersWithoutTerms(showcase()));
    await expand("DUO2");
    fireEvent.click(selectButton()!);
    await waitFor(() => expect(purchase).toHaveBeenCalledTimes(1));
    expect(yesButton()).toBeNull();
  });
});

describe("BillingPage: ссылка ?plan=&days= только предвыбирает", () => {
  it("?plan=HOME3&days=90 — HOME раскрыт, выбран срок 90, оплаты нет", async () => {
    open(offers(showcase(), { current_days_left: 29 }), "/billing?plan=HOME3&days=90");
    await waitFor(() => expect(selectButton()).not.toBeNull());
    expect(document.body.textContent).toContain(ru("billing.forDays", { days: 90 }));
    expect(document.getElementById("plan-HOME3")?.textContent).toContain(ru("billing.select"));
    expect(purchase).not.toHaveBeenCalled();
    expect(payWithBalance).not.toHaveBeenCalled();
  });

  it("?plan=bogus&days=abc — поведение по умолчанию: ничего не раскрыто, срок первый", async () => {
    open(offers(showcase(), { current_days_left: 29 }), "/billing?plan=bogus&days=abc");
    await screen.findByRole("button", { name: /DUO2/ });
    expect(selectButton()).toBeNull();
    await expand("DUO2");
    expect(document.body.textContent).toContain(ru("billing.forDays", { days: 30 }));
  });
});
