import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ExtraTrafficAdminResponse } from "@/api/admin";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

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

// Страница больше не хранит русский текст в коде: подписи приходят из словаря по
// ключам adm.extratraffic.*. Тест сверяется с тем же словарём (и держит админку на
// русском) — иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminExtraTrafficPage />
    </I18nProvider>,
  );

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
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  get.mockReset();
  update.mockReset();
  get.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("AdminExtraTrafficPage", () => {
  it("по умолчанию продажи выключены — тумблер снят", async () => {
    renderPage();
    const toggle = await screen.findByLabelText(rx("adm.extratraffic.enable"));
    expect((toggle as HTMLInputElement).checked).toBe(false);
  });

  it("включили тумблер без цены — страница честно говорит, что продаж всё равно нет", async () => {
    get.mockResolvedValue(answer({ config: { ...answer().config, enabled: true, price_rub: null } }));
    renderPage();
    expect(await screen.findByText(rx("adm.extratraffic.no_price_warn"))).toBeTruthy();
  });

  it("короткие окна обновления — предупреждение на виду", async () => {
    get.mockResolvedValue(
      answer({
        config: { ...answer().config, enabled: true },
        short_window: true,
        strategies: [{ strategy: "DAY", subscriptions: 3 }],
      }),
    );
    renderPage();
    expect(await screen.findByText(rx("adm.extratraffic.short_window_warn"))).toBeTruthy();
  });

  it("длинные окна — лишнего предупреждения нет", async () => {
    get.mockResolvedValue(answer({ config: { ...answer().config, enabled: true } }));
    renderPage();
    await screen.findByLabelText(rx("adm.extratraffic.enable"));
    expect(screen.queryByText(rx("adm.extratraffic.short_window_warn"))).toBeNull();
  });

  it("возврат при отзыве по умолчанию выключен", async () => {
    renderPage();
    const toggle = await screen.findByLabelText(rx("adm.extratraffic.refund"));
    expect((toggle as HTMLInputElement).checked).toBe(false);
  });

  it("сохранение отправляет ровно то, что в форме", async () => {
    update.mockResolvedValue({ config: { ...answer().config, enabled: true }, effective_enabled: true });
    renderPage();
    fireEvent.click(await screen.findByLabelText(rx("adm.extratraffic.enable")));
    fireEvent.click(screen.getByText(ru("adm.extratraffic.save")));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]![0]).toMatchObject({ enabled: true, gb_per_purchase: 50, price_rub: 50 });
    expect(await screen.findByText(rx("adm.extratraffic.saved_open"))).toBeTruthy();
  });

  it("подсказка о шаге тарифов показывает и разницу по устройствам", async () => {
    renderPage();
    expect(
      await screen.findByText(rx("adm.extratraffic.step_range", { from: 200, to: 300 }), {
        exact: false,
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(rx("adm.extratraffic.step_devices", { n: "+1" }), { exact: false }),
    ).toBeTruthy();
  });
});
