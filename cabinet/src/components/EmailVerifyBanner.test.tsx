import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import { ApiError, type MeResponse } from "@/types/api";

/**
 * Плашка «Подтвердите почту» на Главной.
 *
 * ЧТО ЗАПЕРТО. Показ ровно тем, кому есть что подтверждать (почта есть, не
 * подтверждена, бэкенд умеет подтверждение); подтверждение кодом через
 * существующую ручку и исчезновение плашки; повторная отправка с паузой, в том
 * числе когда упёрлись в рейт-лимит.
 */

const request = vi.fn();
const confirm = vi.fn();
vi.mock("@/api/auth", () => ({
  authApi: {
    requestEmailVerification: () => request(),
    confirmEmailVerification: (d: { code: string }) => confirm(d),
  },
}));

let user: MeResponse | null = null;
const refreshMe = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user, refreshMe }) }));

let canVerify = true;
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ can: (key: string) => (key === "email_verify" ? canVerify : true) }),
}));

const { EmailVerifyBanner, RESEND_PAUSE_S } = await import("./EmailVerifyBanner");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const EMAIL = "ivan@example.org";

function me(over: Partial<MeResponse> = {}): MeResponse {
  return {
    telegram_id: null,
    auth_type: "email",
    email: EMAIL,
    is_email_verified: false,
    pending_email: null,
    name: "Иван",
    username: null,
    language: "ru",
    ...over,
  };
}

function renderBanner() {
  return render(
    <I18nProvider>
      <EmailVerifyBanner />
    </I18nProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(STORAGE_KEY, "ru");
  user = me();
  canVerify = true;
  request.mockReset();
  confirm.mockReset();
  refreshMe.mockReset();
  refreshMe.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("плашка неподтверждённой почты: кому показывать", () => {
  it("почта есть и не подтверждена — адрес, поле кода и подсказка про «Спам»", () => {
    renderBanner();
    expect(screen.getByText(ru("emailBanner.title"))).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.text", { email: EMAIL }))).toBeTruthy();
    expect(screen.getByLabelText(ru("emailBanner.codePlaceholder"))).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.spam"))).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.resend"))).toBeTruthy();
    // Код сам не отправляем: плашка видна на каждом заходе на Главную.
    expect(request).not.toHaveBeenCalled();
  });

  it("почта подтверждена — плашки нет", () => {
    user = me({ is_email_verified: true });
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });

  it("почты нет (вход через Telegram) — плашки нет", () => {
    user = me({ auth_type: "telegram", telegram_id: 42, email: null });
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });

  it("подтверждение почты выключено на бэкенде — плашки нет", () => {
    canVerify = false;
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });

  it("не вошёл — плашки нет", () => {
    user = null;
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });
});

describe("плашка неподтверждённой почты: подтверждение", () => {
  it("верный код — существующая ручка подтверждения, профиль перечитан, плашка исчезла", async () => {
    confirm.mockResolvedValue({ success: true, email: EMAIL });
    const { container } = renderBanner();
    fireEvent.change(screen.getByLabelText(ru("emailBanner.codePlaceholder")), { target: { value: " 123456 " } });
    await act(async () => {
      fireEvent.click(screen.getByText(ru("set.confirm")));
    });
    expect(confirm).toHaveBeenCalledWith({ code: "123456" });
    expect(refreshMe).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("");
  });

  it("неверный код — текст ошибки бэкенда, плашка остаётся", async () => {
    confirm.mockRejectedValue(new ApiError(400, "Неверный код"));
    renderBanner();
    fireEvent.change(screen.getByLabelText(ru("emailBanner.codePlaceholder")), { target: { value: "000000" } });
    await act(async () => {
      fireEvent.click(screen.getByText(ru("set.confirm")));
    });
    expect(screen.getByText("Неверный код")).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.title"))).toBeTruthy();
    expect(refreshMe).not.toHaveBeenCalled();
  });

  it("пустое поле — кнопка неактивна, в ручку не ходим", () => {
    renderBanner();
    const button = screen.getByText(ru("set.confirm")).closest("button")!;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(confirm).not.toHaveBeenCalled();
  });
});

describe("плашка неподтверждённой почты: прислать код заново", () => {
  it("отправили — «новый код отправлен», кнопка спит с обратным отсчётом и просыпается", async () => {
    vi.useFakeTimers();
    request.mockResolvedValue({ success: true, target_email: EMAIL, expires_at: "" });
    renderBanner();
    await act(async () => {
      fireEvent.click(screen.getByText(ru("emailBanner.resend")));
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.getByText(ru("emailBanner.sent", { email: EMAIL }))).toBeTruthy();

    const waiting = screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S })).closest("button")!;
    expect(waiting.disabled).toBe(true);
    fireEvent.click(waiting);
    expect(request).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S - 1 }))).toBeTruthy();

    // Отсчёт идёт по секунде: каждый шаг заводит следующий таймер после отрисовки.
    for (let i = 1; i < RESEND_PAUSE_S; i++) await act(() => vi.advanceTimersByTimeAsync(1000));
    const again = screen.getByText(ru("emailBanner.resend")).closest("button")!;
    expect(again.disabled).toBe(false);
    await act(async () => {
      fireEvent.click(again);
    });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("упёрлись в рейт-лимит (429) — показываем ответ и тоже ждём паузу", async () => {
    vi.useFakeTimers();
    request.mockRejectedValue(new ApiError(429, "Слишком много попыток. Повторите через минуту."));
    renderBanner();
    await act(async () => {
      fireEvent.click(screen.getByText(ru("emailBanner.resend")));
    });
    expect(screen.getByText("Слишком много попыток. Повторите через минуту.")).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S })).closest("button")!.disabled).toBe(true);
  });

  it("другая ошибка отправки — сообщение без паузы: повторить можно сразу", async () => {
    request.mockRejectedValue(new ApiError(503, "Почта не настроена"));
    renderBanner();
    await act(async () => {
      fireEvent.click(screen.getByText(ru("emailBanner.resend")));
    });
    expect(screen.getByText("Почта не настроена")).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.resend")).closest("button")!.disabled).toBe(false);
  });
});
