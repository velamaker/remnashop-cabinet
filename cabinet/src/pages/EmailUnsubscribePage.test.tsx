import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import { ApiError } from "@/types/api";

// Страница отписки от месячной сводки. Главное, что здесь заперто: открытие
// ссылки НИЧЕГО не меняет. Ссылки из писем открывают и сканеры почты — отпиши мы
// при открытии, сводка молча выключалась бы у всех, чья почта идёт через фильтр.
const status = vi.fn();
const unsubscribe = vi.fn();
const resubscribe = vi.fn();
vi.mock("@/api/emailOptout", () => ({
  emailOptoutApi: {
    status: (t: string) => status(t),
    unsubscribe: (t: string) => unsubscribe(t),
    resubscribe: (t: string) => resubscribe(t),
  },
}));

const { EmailOptoutPanel } = await import("./EmailUnsubscribePage");

/** Русский текст ключа — тот же, что увидит человек (язык задан в beforeEach). */
const ru = (key: string) => translate(key, {}, "ru");
const TOKEN = "42.0123456789abcdef0123456789abcdef";

function open(token = TOKEN) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <EmailOptoutPanel token={token} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  status.mockReset();
  unsubscribe.mockReset();
  resubscribe.mockReset();
});
afterEach(cleanup);

describe("Отписка от сводки по ссылке из письма", () => {
  it("все тексты страницы переведены (перевод по ключу молча вернул бы сам ключ)", () => {
    for (const key of ["title", "intro", "unsubscribe", "doneTitle", "doneText", "resubscribe", "resubscribedText", "invalid", "failed"]) {
      for (const lang of ["ru", "en"] as const) {
        expect(translate(`emailOptout.${key}`, {}, lang)).not.toBe(`emailOptout.${key}`);
      }
    }
  });

  it("при открытии — только чтение, ни одного POST", async () => {
    status.mockResolvedValue({ subscribed: true });
    open();

    await waitFor(() => expect(screen.getByText(ru("emailOptout.intro"))).toBeInTheDocument());
    expect(status).toHaveBeenCalledWith(TOKEN);
    expect(unsubscribe).not.toHaveBeenCalled();
    expect(resubscribe).not.toHaveBeenCalled();
  });

  it("отписка кнопкой → «Вы отписались» → можно подписаться снова", async () => {
    status.mockResolvedValue({ subscribed: true });
    unsubscribe.mockResolvedValue({ subscribed: false });
    resubscribe.mockResolvedValue({ subscribed: true });
    open();

    fireEvent.click(await screen.findByRole("button", { name: ru("emailOptout.unsubscribe") }));
    await waitFor(() => expect(screen.getByText(ru("emailOptout.doneTitle"))).toBeInTheDocument());
    expect(unsubscribe).toHaveBeenCalledWith(TOKEN);

    fireEvent.click(screen.getByRole("button", { name: ru("emailOptout.resubscribe") }));
    await waitFor(() => expect(screen.getByText(ru("emailOptout.resubscribedText"))).toBeInTheDocument());
    expect(resubscribe).toHaveBeenCalledWith(TOKEN);
  });

  it("уже отписан — сразу «Вы отписались», без повторной отписки", async () => {
    status.mockResolvedValue({ subscribed: false });
    open();

    await waitFor(() => expect(screen.getByText(ru("emailOptout.doneTitle"))).toBeInTheDocument());
    expect(unsubscribe).not.toHaveBeenCalled();
  });

  it("нет токена → «ссылка не работает», в бэкенд не ходим", async () => {
    open("");
    expect(screen.getByText(ru("emailOptout.invalid"))).toBeInTheDocument();
    expect(status).not.toHaveBeenCalled();
  });

  it.each([400, 404, 501])("ответ %i → «ссылка не работает»", async (code) => {
    status.mockRejectedValue(new ApiError(code, "нет"));
    open();
    await waitFor(() => expect(screen.getByText(ru("emailOptout.invalid"))).toBeInTheDocument());
  });

  it("сбой сервера (500) — не «ссылка испорчена», а «попробуйте позже»", async () => {
    status.mockRejectedValue(new ApiError(500, "упало"));
    open();
    await waitFor(() => expect(screen.getByText(ru("emailOptout.failed"))).toBeInTheDocument());
    expect(screen.queryByText(ru("emailOptout.invalid"))).toBeNull();
  });
});
