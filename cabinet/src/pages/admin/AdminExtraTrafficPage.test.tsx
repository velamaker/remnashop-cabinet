import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ExtraTrafficAdminResponse } from "@/api/admin";

/**
 * Страница «Докупка трафика». Запираем ровно то, что владелец должен увидеть ДО
 * того, как откроет продажи:
 *  * включённый тумблер без цены — это всё ещё закрытые продажи, и страница обязана
 *    сказать об этом, а не сделать вид, что всё готово;
 *  * при ежедневном или еженедельном обновлении трафика прибавка живёт меньше суток
 *    или недели — предупреждение обязано быть на виду, иначе он продаст месячную
 *    цену за сутки;
 *  * возврат при отзыве по умолчанию ВЫКЛЮЧЕН (решение владельца).
 */

const get = vi.fn();
const update = vi.fn();
vi.mock("@/api/admin", () => ({
  extraTrafficAdminApi: { get: () => get(), update: (d: unknown) => update(d) },
}));

const { AdminExtraTrafficPage } = await import("./AdminExtraTrafficPage");

const answer = (over: Partial<ExtraTrafficAdminResponse> = {}): ExtraTrafficAdminResponse => ({
  config: {
    enabled: false,
    gb_per_purchase: 50,
    price_rub: 50,
    min_amount_rub: 10,
    show_from_percent: 70,
    min_hours_left: 2,
    max_gb_per_window: 1000,
    notify_users: true,
    notify_admins: true,
    notify_limited: true,
    refund_on_revoke: false,
  },
  effective_enabled: false,
  hint: [{ from_gb: 200, to_gb: 300, diff_30d_rub: 100, device_diff: 1 }],
  strategies: [{ strategy: "MONTH_ROLLING", subscriptions: 24 }],
  short_window: false,
  summary: { applied_30d: 0, gb_30d: 0, amount_30d: 0, credited_open: 0, active_grants: 0 },
  ...over,
});

beforeEach(() => {
  get.mockReset();
  update.mockReset();
  get.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("AdminExtraTrafficPage", () => {
  it("по умолчанию продажи выключены — тумблер снят", async () => {
    render(<AdminExtraTrafficPage />);
    const toggle = await screen.findByLabelText(/Продавать докупку трафика/);
    expect((toggle as HTMLInputElement).checked).toBe(false);
  });

  it("включили тумблер без цены — страница честно говорит, что продаж всё равно нет", async () => {
    get.mockResolvedValue(answer({ config: { ...answer().config, enabled: true, price_rub: null } }));
    render(<AdminExtraTrafficPage />);
    expect(await screen.findByText(/Цена не задана/)).toBeTruthy();
  });

  it("короткие окна обновления — предупреждение на виду", async () => {
    get.mockResolvedValue(
      answer({
        config: { ...answer().config, enabled: true },
        short_window: true,
        strategies: [{ strategy: "DAY", subscriptions: 3 }],
      }),
    );
    render(<AdminExtraTrafficPage />);
    expect(await screen.findByText(/каждый день или каждую неделю/)).toBeTruthy();
  });

  it("длинные окна — лишнего предупреждения нет", async () => {
    get.mockResolvedValue(answer({ config: { ...answer().config, enabled: true } }));
    render(<AdminExtraTrafficPage />);
    await screen.findByLabelText(/Продавать докупку трафика/);
    expect(screen.queryByText(/каждый день или каждую неделю/)).toBeNull();
  });

  it("возврат при отзыве по умолчанию выключен", async () => {
    render(<AdminExtraTrafficPage />);
    const toggle = await screen.findByLabelText(/возвращать деньги на баланс/);
    expect((toggle as HTMLInputElement).checked).toBe(false);
  });

  it("сохранение отправляет ровно то, что в форме", async () => {
    update.mockResolvedValue({ config: { ...answer().config, enabled: true }, effective_enabled: true });
    render(<AdminExtraTrafficPage />);
    fireEvent.click(await screen.findByLabelText(/Продавать докупку трафика/));
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]![0]).toMatchObject({ enabled: true, gb_per_purchase: 50, price_rub: 50 });
    expect(await screen.findByText(/Докупка трафика открыта/)).toBeTruthy();
  });

  it("подсказка о шаге тарифов показывает и разницу по устройствам", async () => {
    render(<AdminExtraTrafficPage />);
    expect(await screen.findByText(/200 → 300 ГБ/)).toBeTruthy();
  });
});
