import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { RenewalDiscountConfig, RenewalDiscountPreview, RenewalDiscountStats } from "@/api/admin";

// Страница «Скидка до окончания подписки». Заперто то, что защищает от раздачи
// скидок вслепую:
//  • предпросмотр говорит, что ничего не выдаёт, и печатает причины отказов
//    человеческими словами;
//  • «Отозвать активные скидки» без второго нажатия API не зовёт;
//  • «пример себе» прямо говорит, что скидка не выдана;
//  • на бэкенде без этой механики (501) блоков нет, а не «Не удалось загрузить».
const config = (over: Partial<RenewalDiscountConfig> = {}): RenewalDiscountConfig => ({
  enabled: false,
  percent: 10,
  days_before: 5,
  lifetime_hours: 120,
  cooldown_days: 90,
  skip_early_renewers: true,
  min_days_before: 4,
  note: null,
  ...over,
});

const previewData: RenewalDiscountPreview = {
  window_from: "2026-09-22T00:00:00Z",
  window_to: "2026-10-22T12:00:00Z",
  horizon_days: 30,
  examined: 3,
  would_grant: 1,
  truncated: false,
  skipped: { not_paid: 2 },
  reason_labels: { not_paid: "ни разу не платил (подарок, промокод, импорт)" },
  sample: [
    {
      user_id: 42,
      expire_at: "2026-10-01T00:00:00Z",
      grant_at: "2026-09-26T00:00:00Z",
      channels: { telegram: true, push: false, email: false },
      would_grant: true,
      reason: null,
    },
  ],
  message: { telegram_html: "<b>🎁 Скидка 10% на продление</b>\n\nПодписка закончится через 5 дней.", push_title: "", push_body: "" },
};

const stats = (over: Partial<RenewalDiscountStats> = {}): RenewalDiscountStats => ({
  period_days: 90,
  granted: 4,
  used: 1,
  expired: 1,
  active: 2,
  revoked: 0,
  paid_rub: 449,
  discount_given_rub: 50,
  tg_failed: 0,
  push_delivered: 1,
  recent: [],
  ...over,
});

let getConfig: () => Promise<RenewalDiscountConfig> = () => Promise.resolve(config());
let getStats: () => Promise<RenewalDiscountStats> = () => Promise.resolve(stats());
const preview = vi.fn();
const testSend = vi.fn();
const revokeActive = vi.fn();

vi.mock("@/api/admin", () => ({
  renewalDiscountAdminApi: {
    get: () => getConfig(),
    update: (data: unknown) => Promise.resolve(data),
    preview: (h: number) => preview(h),
    stats: () => getStats(),
    testSend: () => testSend(),
    revokeActive: () => revokeActive(),
  },
}));

const { default: AdminRenewalDiscountPage } = await import("./AdminRenewalDiscountPage");

beforeEach(() => {
  getConfig = () => Promise.resolve(config());
  getStats = () => Promise.resolve(stats());
  preview.mockReset();
  testSend.mockReset();
  revokeActive.mockReset();
});
afterEach(cleanup);

describe("Скидка до окончания подписки: страница", () => {
  it("предпросмотр ничего не выдаёт и печатает причины словами", async () => {
    preview.mockResolvedValue(previewData);
    render(<AdminRenewalDiscountPage />);

    expect(screen.getByText(/Ничего не выдаётся и не отправляется/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Горизонт"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить" }));

    await waitFor(() => expect(screen.getByText(/Осмотрено 3, получат скидку 1/)).toBeInTheDocument());
    expect(preview).toHaveBeenCalledWith(60);
    expect(screen.getByText("ни разу не платил (подарок, промокод, импорт) — 2")).toBeInTheDocument();
    expect(screen.getByText(/Скидка 10% на продление/)).toBeInTheDocument();
  });

  it("«Отозвать» без второго нажатия API не зовёт", async () => {
    revokeActive.mockResolvedValue({ revoked: 2 });
    render(<AdminRenewalDiscountPage />);
    const revoke = await screen.findByRole("button", { name: "Отозвать активные скидки" });

    fireEvent.click(revoke);
    expect(revokeActive).not.toHaveBeenCalled();
    expect(screen.getByText(/Отозвать 2 активные скидки\?/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(revokeActive).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Отозвать активные скидки" }));
    fireEvent.click(screen.getByRole("button", { name: "Да, отозвать" }));
    await waitFor(() => expect(revokeActive).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("Отозвано: 2")).toBeInTheDocument());
  });

  it("пример себе прямо говорит, что скидка не выдана", async () => {
    testSend.mockResolvedValue({ telegram: "sent", push: 0 });
    render(<AdminRenewalDiscountPage />);

    fireEvent.click(screen.getByRole("button", { name: "Прислать пример себе" }));
    await waitFor(() =>
      expect(screen.getByText("Пример отправлен вам в Telegram. Скидка не выдана.")).toBeInTheDocument(),
    );
  });

  it("бэкенд без этой механики (501) — блоков нет, ошибок тоже", async () => {
    getConfig = () => Promise.reject(new ApiError(501, "нет"));
    getStats = () => Promise.reject(new ApiError(501, "нет"));
    render(<AdminRenewalDiscountPage />);

    await new Promise((r) => setTimeout(r, 0));
    await waitFor(() => expect(screen.queryByText(/Итоги за/)).toBeNull());
    expect(screen.queryByText("Не удалось загрузить")).toBeNull();
    expect(screen.queryByText("Не удалось загрузить итоги")).toBeNull();
    // Заголовок страницы и настройки: карточка настроек скрыта (есть только h1).
    expect(screen.getAllByText("Скидка до окончания подписки")).toHaveLength(1);
  });
});
