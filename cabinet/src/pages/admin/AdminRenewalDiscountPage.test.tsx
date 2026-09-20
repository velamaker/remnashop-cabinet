import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { RenewalDiscountConfig, RenewalDiscountPreview, RenewalDiscountStats } from "@/api/admin";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// Подписи страницы — из словаря: сверяем не с русским текстом, а с тем, что даёт
// перевод. Иначе тест ловил бы правку формулировки, а не поломку страницы.
const ru = (key: string, vars?: Record<string, string | number>) =>
  translate(`adm.renewaldiscount.${key}`, vars, "ru");

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

// Карточка настроек на этой странице берёт подписи из словаря — рендерим её
// внутри провайдера языка и держим кабинет на русском.
const renderPage = () =>
  render(
    <I18nProvider>
      <AdminRenewalDiscountPage />
    </I18nProvider>,
  );

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
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
    renderPage();

    expect(
      screen.getByText(ru("preview_hint", { days: ru("days_many", { n: 30 }) })),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ru("horizon")), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: ru("check") }));

    await waitFor(() =>
      expect(screen.getByText(ru("summary", { examined: 3, granted: 1 }))).toBeInTheDocument(),
    );
    expect(preview).toHaveBeenCalledWith(60);
    expect(screen.getByText("ни разу не платил (подарок, промокод, импорт) — 2")).toBeInTheDocument();
    expect(screen.getByText(/Скидка 10% на продление/)).toBeInTheDocument();
  });

  it("«Отозвать» без второго нажатия API не зовёт", async () => {
    revokeActive.mockResolvedValue({ revoked: 2 });
    renderPage();
    const revoke = await screen.findByRole("button", { name: ru("revoke") });

    fireEvent.click(revoke);
    expect(revokeActive).not.toHaveBeenCalled();
    expect(screen.getByText(ru("revoke_confirm_few", { n: 2 }))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: ru("cancel") }));
    expect(revokeActive).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: ru("revoke") }));
    fireEvent.click(screen.getByRole("button", { name: ru("revoke_yes") }));
    await waitFor(() => expect(revokeActive).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(ru("revoked", { n: 2 }))).toBeInTheDocument());
  });

  it("пример себе прямо говорит, что скидка не выдана", async () => {
    testSend.mockResolvedValue({ telegram: "sent", push: 0 });
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: ru("send_example") }));
    await waitFor(() => expect(screen.getByText(ru("example_sent"))).toBeInTheDocument());
  });

  it("бэкенд без этой механики (501) — блоков нет, ошибок тоже", async () => {
    getConfig = () => Promise.reject(new ApiError(501, "нет"));
    getStats = () => Promise.reject(new ApiError(501, "нет"));
    renderPage();

    await new Promise((r) => setTimeout(r, 0));
    await waitFor(() =>
      expect(screen.queryByText(ru("stats_title", { days: ru("days_many", { n: 90 }) }))).toBeNull(),
    );
    expect(screen.queryByText("Не удалось загрузить")).toBeNull();
    expect(screen.queryByText(ru("stats_error"))).toBeNull();
    // Заголовок страницы и настройки: карточка настроек скрыта (есть только h1).
    expect(screen.getAllByText(ru("title"))).toHaveLength(1);
  });
});
