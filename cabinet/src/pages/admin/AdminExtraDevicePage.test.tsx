import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Страница настроек денежной функции: что именно уходит на бэкенд и что видит
// владелец, пока цена не задана. Продажи по умолчанию ВЫКЛЮЧЕНЫ — выкатка образа
// сама по себе брать деньги не должна.
const getMock = vi.fn();
const updateMock = vi.fn();
vi.mock("@/api/admin", () => ({
  extraDeviceAdminApi: {
    get: () => getMock(),
    update: (body: unknown) => updateMock(body),
  },
}));

const { AdminExtraDevicePage } = await import("./AdminExtraDevicePage");

const config = (over: Record<string, unknown> = {}) => ({
  enabled: false,
  price_rub_30d: 100,
  min_amount_rub: 10,
  min_days_left: 3,
  max_extra: 2,
  remove_excess_devices: false,
  notify_users: true,
  notify_admins: true,
  ...over,
});

const answer = (over: Record<string, unknown> = {}) => ({
  config: config(),
  effective_enabled: false,
  hint: [{ from_devices: 1, to_devices: 2, diff_30d_rub: 150, traffic_diff_gb: 50 }],
  summary: { applied_30d: 0, amount_30d: 0, active_slots: 0, credited_open: 0 },
  ...over,
});

beforeEach(() => {
  getMock.mockReset();
  updateMock.mockReset();
  getMock.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("AdminExtraDevicePage", () => {
  it("цена владельца видна, продажи по умолчанию выключены", async () => {
    render(<AdminExtraDevicePage />);
    const price = (await screen.findByLabelText(/Цена за 1 устройство/)) as HTMLInputElement;
    expect(price.value).toBe("100");
    const toggle = screen.getByLabelText(/Продавать докупку/) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it("включили без цены — предупреждение, что продажи всё равно закрыты", async () => {
    getMock.mockResolvedValue(answer({ config: config({ enabled: true, price_rub_30d: null }) }));
    render(<AdminExtraDevicePage />);
    await waitFor(() =>
      expect(document.body.textContent).toContain("Цена не задана"),
    );
  });

  it("сохранение шлёт числа, а пустая цена уходит как null", async () => {
    updateMock.mockResolvedValue({ config: config({ price_rub_30d: null }), effective_enabled: false });
    render(<AdminExtraDevicePage />);
    const price = (await screen.findByLabelText(/Цена за 1 устройство/)) as HTMLInputElement;
    fireEvent.change(price, { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/Максимум докупленных мест/), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    const body = updateMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(body.price_rub_30d).toBeNull();
    expect(body.max_extra).toBe(3);
    await waitFor(() => expect(document.body.textContent).toContain("Докупка закрыта"));
  });

  it("отключение устройств — отдельный тумблер и по умолчанию выключен", async () => {
    render(<AdminExtraDevicePage />);
    const toggle = (await screen.findByLabelText(/Отключать устройства/)) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it("подсказка о шаге тарифов показывает и трафик — цену ставит человек, не формула", async () => {
    render(<AdminExtraDevicePage />);
    await waitFor(() => expect(document.body.textContent).toContain("150 ₽"));
    expect(document.body.textContent).toContain("+50 ГБ");
  });
});
