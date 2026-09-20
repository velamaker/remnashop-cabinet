import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { ApiError } from "@/types/api";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

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

// Подписи страницы живут в словаре (ключи adm.notifications.*), а не в коде:
// тест берёт их оттуда же и держит кабинет на русском — иначе он сверялся бы с
// копией строки, а не с тем, что видит админ.
const ru = (key: string) => translate(key, undefined, "ru");

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminNotificationsPage />
    </I18nProvider>,
  );

/** Карточка тумблера rich-вида — по её заголовку. */
const richCard = () => screen.queryByText(ru("adm.notifications.rich_title"));

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  updateSettings.mockClear();
});
afterEach(cleanup);

describe("Тумблер «новый вид уведомлений»", () => {
  it("наш бот отдал поле → тумблер есть и шлёт ТОЛЬКО свой ключ", async () => {
    settings = () =>
      Promise.resolve({ admin_push_enabled: true, admin_rich_enabled: true });
    renderPage();

    await waitFor(() => expect(richCard()).not.toBeNull());
    const toggle = richCard()!.closest("div.rounded-2xl")!.querySelector("button")!;
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(toggle);
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith({ admin_rich_enabled: false }));
    await waitFor(() => expect(toggle.getAttribute("aria-pressed")).toBe("false"));
  });

  it("бэкенд поля не отдал (старая версия бота) → тумблера нет вовсе", async () => {
    settings = () => Promise.resolve({ admin_push_enabled: true });
    renderPage();

    // Дожидаемся, пока страница дорисуется (список уже загрузился), и только
    // потом проверяем отсутствие — иначе тест пройдёт «до» появления карточки.
    await waitFor(() => expect(screen.queryByText(ru("adm.notifications.empty"))).not.toBeNull());
    expect(richCard()).toBeNull();
  });

  it("адаптер «Бедолаги»: ручки нет (501) → экран цел, мёртвого тумблера нет", async () => {
    settings = () => Promise.reject(new ApiError(501, "Адаптер пока не умеет"));
    renderPage();

    await waitFor(() => expect(screen.queryByText(ru("adm.notifications.empty"))).not.toBeNull());
    expect(richCard()).toBeNull();
    // Ошибку 501 экран не показывает: настройка читается с .catch, как и раньше.
    expect(screen.queryByText(/Адаптер пока не умеет/)).toBeNull();
  });
});
