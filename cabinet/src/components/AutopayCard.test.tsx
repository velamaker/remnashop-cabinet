import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Карточка автопродления.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. Автопродление списывает с РУБЛЁВОГО баланса, а он больше нуля
 * у трёх человек из 1124. Значит включённый тумблер при пустом балансе — обещание,
 * которого мы не выполним: подписка не продлится, и человек об этом не узнает.
 * Карточка обязана сказать это прямо и увести пополнить.
 */

const get = vi.fn();
const setAutopay = vi.fn();

vi.mock("@/api/balance", () => ({
  balanceApi: {
    get: () => get(),
    setAutopay: (v: boolean) => setAutopay(v),
  },
}));

const { AutopayCard } = await import("./AutopayCard");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

function show(props: Record<string, unknown> = {}) {
  render(
    <MemoryRouter>
      <I18nProvider>
        <AutopayCard {...props} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

const balance = (over: Record<string, unknown> = {}) => ({
  balance: 500,
  points: 0,
  point_value_rub: 1,
  total_spent: 0,
  total_purchases: 0,
  autopay_enabled: true,
  autopay_days_before: 3,
  ...over,
});

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  get.mockReset();
  setAutopay.mockReset();
  get.mockResolvedValue(balance());
  setAutopay.mockImplementation(async (v: boolean) => ({ success: true, autopay_enabled: v }));
});
afterEach(() => cleanup());

describe("карточка автопродления", () => {
  it("включено — называет срок списания и остаток", async () => {
    show();
    await waitFor(() => expect(screen.getByText(ru("autopay.title"))).toBeTruthy());
    expect(screen.getByText(ru("autopay.willCharge", { days: 3, sum: "500" }))).toBeTruthy();
  });

  it("бот не прислал срок — обещаем без числа, а не выдумываем своё", async () => {
    get.mockResolvedValue(balance({ autopay_days_before: undefined }));
    show();
    await waitFor(() => expect(screen.getByText(ru("autopay.willChargeNoDays", { sum: "500" }))).toBeTruthy());
  });

  it("включено при пустом балансе — предупреждаем и ведём пополнить", async () => {
    get.mockResolvedValue(balance({ balance: 0 }));
    show();
    await waitFor(() => expect(screen.getByText(ru("autopay.emptyBalance"))).toBeTruthy());
    const link = screen.getByText(ru("autopay.topup")).closest("a");
    expect(link?.getAttribute("href")).toBe("/balance");
  });

  it("выключено — предупреждения о балансе нет", async () => {
    get.mockResolvedValue(balance({ autopay_enabled: false, balance: 0 }));
    show();
    await waitFor(() => expect(screen.getByText(ru("autopay.off"))).toBeTruthy());
    expect(screen.queryByText(ru("autopay.emptyBalance"))).toBeNull();
  });

  it("переключение шлёт запрос и обновляет вид", async () => {
    get.mockResolvedValue(balance({ autopay_enabled: false }));
    show();
    await waitFor(() => expect(screen.getByRole("switch")).toBeTruthy());
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(setAutopay).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true"));
  });

  it("баланс не отдался — карточки нет вовсе (лучше молчать, чем врать)", async () => {
    get.mockRejectedValue(new Error("нет ручки"));
    show();
    await waitFor(() => expect(screen.queryByText(ru("autopay.title"))).toBeNull());
  });

  it("данные пришли пропсом — своего запроса не делаем", async () => {
    show({ data: balance() });
    await waitFor(() => expect(screen.getByText(ru("autopay.title"))).toBeTruthy());
    expect(get).not.toHaveBeenCalled();
  });
});
