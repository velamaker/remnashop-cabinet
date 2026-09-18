import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { Appearance } from "@/api/appearance";
import type { FeatureKey } from "@/lib/features";
import { devicesOf, device, offers, plan, sub } from "@/test/offersFixtures";

// Порядок действий перед концом места — решение владельца: СНАЧАЛА тариф побольше,
// потом продление. Здесь проверяется именно порядок, а не наличие кнопок.
const look = () =>
  ({ brand_name: "X", bot_capabilities: ["extra_device", "device_upsell"] }) as unknown as Appearance;
let appearance: Appearance | null = look();
vi.mock("@/contexts/BrandingContext", async () => {
  const { canFeature } = await import("@/lib/features");
  return {
    useBranding: () => ({ appearance, can: (key: FeatureKey) => canFeature(appearance, key) }),
  };
});

const extraMock = vi.fn();
const offersMock = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: {
    extraDevice: () => extraMock(),
    offers: () => offersMock(),
    buyExtraDevice: () => Promise.resolve({ result: "applied" }),
  },
}));

const { ExtraDevicesPanel } = await import("./ExtraDevicesPanel");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const DAY = 86400_000;

const iso = (days: number) => new Date(Date.now() + days * DAY).toISOString();

const answer = (endsInDays: number) => ({
  enabled: true,
  currency_symbol: "₽",
  balance: "500",
  device_limit: 2,
  plan_device_limit: 1,
  subscription_expire_at: iso(60),
  removes_excess: true,
  gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
  new: { available: false, reason: "max_reached" },
  slots: [
    {
      slot_id: 1,
      ends_at: iso(endsInDays),
      extend: { amount: "100", until: iso(endsInDays + 30), days: 30 },
    },
  ],
});

// Трафик у тарифов не меньше текущего — иначе «побольше» оказался бы шагом назад,
// и подбор тарифа честно вернул бы null.
const showcase = () => [
  plan("SOLO1", 1, { type: "RENEW", traffic: 100 }),
  plan("DUO2", 2, { traffic: 200 }),
  plan("HOME3", 3, { traffic: 300 }),
];

function view() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <ExtraDevicesPanel
          subscription={sub({ device_limit: 2, traffic_limit: 100, plan_name: "SOLO1", expire_at: iso(60) })}
          devices={devicesOf([device()], 2)}
          cardShowsOffer={false}
        />
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  extraMock.mockReset();
  offersMock.mockReset();
  offersMock.mockResolvedValue(
    offers(showcase(), { plan_change_keeps_days: true, plan_change_carry_active: true }),
  );
});
afterEach(cleanup);

describe("ExtraDevicesPanel: порядок действий перед концом места", () => {
  it("место кончается на днях — тариф побольше идёт ПЕРЕД продлением", async () => {
    extraMock.mockResolvedValue(answer(2));
    const { container } = view();
    // Ближайший тариф, где устройств БОЛЬШЕ текущего лимита (2), — HOME3.
    const upgrade = await screen.findByRole("link", {
      name: new RegExp(ru("extraDevice.upgradeCta", { plan: "HOME3" })),
    });
    const extend = await screen.findByRole("button", { name: /Продлить до/ });
    // Решение владельца: сначала тариф (там и трафик, и место навсегда), потом продление.
    expect(upgrade.compareDocumentPosition(extend) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.textContent).toContain("Место работает до");
  });

  it("до конца места далеко — за витриной не ходим и тариф не предлагаем", async () => {
    extraMock.mockResolvedValue(answer(20));
    view();
    await screen.findByRole("button", { name: /Продлить до/ });
    expect(offersMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/HOME3/)).toBeNull();
  });

  it("бот без токена extra_device — панели нет и запроса нет", async () => {
    appearance = { brand_name: "X", bot_capabilities: [] as string[] } as unknown as Appearance;
    const { container } = view();
    await new Promise((r) => setTimeout(r, 0));
    expect(extraMock).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
    appearance = look();
  });

  it("продажи закрыты — панель молчит", async () => {
    extraMock.mockResolvedValue({ enabled: false });
    const { container } = view();
    await waitFor(() => expect(extraMock).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
