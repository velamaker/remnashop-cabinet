import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { ApiError } from "@/types/api";

// Что умеет бэкенд — задаёт тест: именно от этого зависит, рисовать ли «Очистить».
let features: Record<string, boolean> = {};
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ can: (key: string) => features[key] !== false }),
}));

const clear = vi.fn();
vi.mock("@/api/notifications", () => ({
  notificationsApi: {
    list: () => Promise.resolve({ unread: 1, items: [{ id: 1, title: "Привет", body: "", url: "", is_read: false, created_at: null }] }),
    unreadCount: () => Promise.resolve({ unread: 1 }),
    markRead: () => Promise.resolve({ ok: true }),
    clear: () => clear(),
  },
}));

const { NotificationBell } = await import("./NotificationBell");

function open() {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <NotificationBell />
      </I18nProvider>
    </MemoryRouter>,
  );
}

/** Кнопка «Очистить» — единственная с иконкой корзины в шапке панели. */
const clearButton = () => document.querySelector("button.hover\\:text-danger");

beforeEach(() => {
  features = {};
  clear.mockReset();
});
afterEach(cleanup);

describe("NotificationBell: «Очистить» не должна врать", () => {
  it("бэкенд умеет чистить (поля нет — наш бот) → кнопка есть, список гаснет", async () => {
    clear.mockResolvedValue({ ok: true });
    open();
    fireEvent.click(screen.getByRole("button", { name: /уведомлен|notification/i }));
    await waitFor(() => expect(screen.getByText("Привет")).toBeInTheDocument());
    fireEvent.click(clearButton()!);
    await waitFor(() => expect(screen.queryByText("Привет")).toBeNull());
  });

  it("бэкенд чистить не умеет (notifications_clear:false) → кнопки нет вовсе", async () => {
    features = { notifications_clear: false };
    open();
    fireEvent.click(screen.getByRole("button", { name: /уведомлен|notification/i }));
    await waitFor(() => expect(screen.getByText("Привет")).toBeInTheDocument());
    expect(clearButton()).toBeNull();
  });

  it("501 на очистку → список остаётся на месте, кнопка исчезает", async () => {
    clear.mockRejectedValue(new ApiError(501, "нет соответствия"));
    open();
    fireEvent.click(screen.getByRole("button", { name: /уведомлен|notification/i }));
    await waitFor(() => expect(screen.getByText("Привет")).toBeInTheDocument());
    fireEvent.click(clearButton()!);
    await waitFor(() => expect(clearButton()).toBeNull());
    // Главное: обещания «очищено» не было — уведомление на экране.
    expect(screen.getByText("Привет")).toBeInTheDocument();
  });

  it("разовый сбой (500) кнопку не прячет и список не трогает", async () => {
    clear.mockRejectedValue(new ApiError(500, "упало"));
    open();
    fireEvent.click(screen.getByRole("button", { name: /уведомлен|notification/i }));
    await waitFor(() => expect(screen.getByText("Привет")).toBeInTheDocument());
    fireEvent.click(clearButton()!);
    await waitFor(() => expect(clear).toHaveBeenCalled());
    expect(clearButton()).not.toBeNull();
    expect(screen.getByText("Привет")).toBeInTheDocument();
  });
});
