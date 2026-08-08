import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { ApiError } from "@/types/api";

// Что отдаёт бэкенд на GET /notifications/settings — задаёт тест. Именно от
// этого зависит, рисовать ли тумблер rich-вида: на установке поверх чужого бота
// («Бедолага», адаптер) ручки нет вовсе и переключателя быть не должно.
let settings: () => Promise<unknown> = () => Promise.resolve({ admin_push_enabled: true });
const updateSettings = vi.fn(
  (body: Record<string, boolean>) => Promise.resolve({ admin_push_enabled: true, ...body }),
);

vi.mock("@/api/admin", () => ({
  notificationsAdminApi: {
    list: () => Promise.resolve({ items: [] }),
    clear: () => Promise.resolve({ ok: true }),
    getSettings: () => settings(),
    updateSettings: (body: Record<string, boolean>) => updateSettings(body),
  },
}));

const { default: AdminNotificationsPage } = await import("./AdminNotificationsPage");

/** Карточка тумблера rich-вида — по её заголовку. */
const richCard = () => screen.queryByText("Новый вид уведомлений в Telegram");

beforeEach(() => {
  updateSettings.mockClear();
});
afterEach(cleanup);

describe("Тумблер «новый вид уведомлений»", () => {
  it("наш бот отдал поле → тумблер есть и шлёт ТОЛЬКО свой ключ", async () => {
    settings = () =>
      Promise.resolve({ admin_push_enabled: true, admin_rich_enabled: true });
    render(<AdminNotificationsPage />);

    await waitFor(() => expect(richCard()).not.toBeNull());
    const toggle = richCard()!.closest("div.rounded-2xl")!.querySelector("button")!;
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(toggle);
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ admin_rich_enabled: false }));
    await waitFor(() => expect(toggle.getAttribute("aria-pressed")).toBe("false"));
  });

  it("бэкенд поля не отдал (старая версия бота) → тумблера нет вовсе", async () => {
    settings = () => Promise.resolve({ admin_push_enabled: true });
    render(<AdminNotificationsPage />);

    // Дожидаемся, пока страница дорисуется (список уже загрузился), и только
    // потом проверяем отсутствие — иначе тест пройдёт «до» появления карточки.
    await waitFor(() => expect(screen.queryByText("Уведомлений пока нет")).not.toBeNull());
    expect(richCard()).toBeNull();
  });

  it("адаптер «Бедолаги»: ручки нет (501) → экран цел, мёртвого тумблера нет", async () => {
    settings = () => Promise.reject(new ApiError(501, "Адаптер пока не умеет"));
    render(<AdminNotificationsPage />);

    await waitFor(() => expect(screen.queryByText("Уведомлений пока нет")).not.toBeNull());
    expect(richCard()).toBeNull();
    // Ошибку 501 экран не показывает: настройка читается с .catch, как и раньше.
    expect(screen.queryByText(/Адаптер пока не умеет/)).toBeNull();
  });
});
