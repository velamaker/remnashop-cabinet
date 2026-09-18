import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import type { ExtraTrafficResponse } from "@/types/api";

/**
 * Кнопка докупки трафика: два шага и ОДИН `request_id`.
 *
 * Два шага — потому что покупка одним кликом на сумму, которую человек не называл,
 * не то, чего ждут от кнопки в карточке расхода. Один ключ на подтверждение —
 * потому что двойной клик и повтор после разорванного соединения обязаны дать одну
 * прибавку, а не две: ключ держит сервер, но кабинет не должен присылать разные.
 */

const buy = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: { buyExtraTraffic: (d: unknown) => buy(d) },
}));

const { ExtraTrafficOffer } = await import("./ExtraTrafficOffer");
const { offerView } = await import("@/lib/extraTraffic");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const data = (over: Partial<ExtraTrafficResponse> = {}): ExtraTrafficResponse => ({
  enabled: true,
  currency_symbol: "₽",
  gb: 50,
  price: "50",
  balance: "500",
  traffic_limit_gb: 300,
  plan_traffic_limit_gb: 300,
  used_bytes: 280 * 1024 ** 3,
  strategy: "MONTH_ROLLING",
  resets_at: "2026-10-07T00:10:00Z",
  gateways: [{ gateway_type: "YOOMONEY", currency_symbol: "₽" }],
  offer: { available: true, until: "2026-10-07T00:10:00Z", hours_left: 400 },
  ...over,
});

function show(over: Partial<ExtraTrafficResponse> = {}) {
  const payload = data(over);
  const view = offerView(payload)!;
  render(
    <MemoryRouter>
      <I18nProvider>
        <ExtraTrafficOffer data={payload} offer={view} />
      </I18nProvider>
    </MemoryRouter>,
  );
  return view;
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  buy.mockReset();
  buy.mockResolvedValue({ result: "applied", gb: 50, traffic_limit_gb: 350, until: "2026-10-07T00:10:00Z" });
});
afterEach(cleanup);

describe("ExtraTrafficOffer", () => {
  it("первый клик только спрашивает — ни одного платежа", () => {
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    expect(buy).not.toHaveBeenCalled();
    expect(screen.getByText(ru("extraTraffic.payBalance"))).toBeTruthy();
  });

  it("срок назван ДО оплаты — и в кнопке, и в подтверждении", () => {
    show();
    // Под кнопкой — строка «действует до обновления трафика …».
    expect(screen.getByText(/Действует до обновления трафика/)).toBeTruthy();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    expect(screen.getByText(/Трафик обновится/)).toBeTruthy();
  });

  it("меньше суток до обновления — отдельная строка-предупреждение", () => {
    show({ offer: { available: true, until: "2026-09-19T00:10:00Z", hours_left: 10 } });
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    expect(screen.getByText(/меньше суток/)).toBeTruthy();
  });

  it("двойной клик по оплате — один и тот же request_id", async () => {
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    const pay = screen.getByText(ru("extraTraffic.payBalance"));
    fireEvent.click(pay);
    fireEvent.click(pay);
    await waitFor(() => expect(buy).toHaveBeenCalled());
    const ids = buy.mock.calls.map((c) => (c[0] as { request_id: string }).request_id);
    expect(new Set(ids).size).toBe(1);
  });

  it("покупка называет объём и ожидаемую сумму — сервер сверит их со своими", async () => {
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    fireEvent.click(screen.getByText(ru("extraTraffic.payBalance")));
    await waitFor(() => expect(buy).toHaveBeenCalled());
    expect(buy.mock.calls[0]![0]).toMatchObject({
      pay: "balance",
      expected_amount: "50",
      expected_gb: 50,
    });
  });

  it("условия изменились — новый ключ, человек подтверждает заново", async () => {
    buy.mockResolvedValue({
      result: "price_changed",
      quote: data({ price: "70", gb: 40 }),
    });
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    fireEvent.click(screen.getByText(ru("extraTraffic.payBalance")));
    await waitFor(() => expect(screen.getByText(/Условия обновились/)).toBeTruthy());
  });

  it("не хватило баланса — говорим прямо, а не «не получилось»", async () => {
    buy.mockResolvedValue({ result: "insufficient_balance", balance: "10" });
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    fireEvent.click(screen.getByText(ru("extraTraffic.payBalance")));
    await waitFor(() => expect(screen.getByText(/не хватает/)).toBeTruthy());
  });

  it("доступ вернулся вместе с трафиком — говорим об этом отдельно", async () => {
    buy.mockResolvedValue({
      result: "applied",
      gb: 50,
      traffic_limit_gb: 350,
      unlocked: true,
      until: "2026-10-07T00:10:00Z",
    });
    show();
    fireEvent.click(screen.getByText(ru("extraTraffic.offer", { gb: 50, price: "50 ₽" })));
    fireEvent.click(screen.getByText(ru("extraTraffic.payBalance")));
    await waitFor(() => expect(screen.getByText(/доступ восстановлен/)).toBeTruthy());
  });

  it("платить нечем — карточки нет вовсе, а не кнопка в никуда", () => {
    const payload = data({ balance: "1", gateways: [] });
    const view = offerView(payload)!;
    const { container } = render(
      <MemoryRouter>
        <I18nProvider>
          <ExtraTrafficOffer data={payload} offer={view} />
        </I18nProvider>
      </MemoryRouter>,
    );
    expect(container.textContent).toBe("");
  });
});
