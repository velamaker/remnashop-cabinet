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
 * подтверждена, бэкенд умеет подтверждение, почта на установке включена).
 * ДВА ШАГА: пока код не запрошен, плашка не просит ввести код из письма, которого
 * никто не отправлял, — только предлагает прислать его; поле для кода, «пришло на …»
 * и подсказка про «Спам» — после успешной отправки, и перезагрузка их не прячет.
 * Подтверждение кодом через существующую ручку и исчезновение плашки; повторная
 * отправка с паузой, в том числе когда упёрлись в рейт-лимит; 503 — почта
 * выключена, плашка уходит до конца сессии.
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
let emailAuthEnabled = true;
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({
    can: (key: string) => (key === "email_verify" ? canVerify : true),
    emailAuthEnabled,
  }),
}));

const { EmailVerifyBanner, RESEND_PAUSE_S, SENT_KEY, OFF_KEY } = await import("./EmailVerifyBanner");

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

/** Код на этот адрес уже запрашивали в этой вкладке, пауза повтора давно прошла. */
function markSent(to = EMAIL, agoMs = 10 * 60_000) {
  sessionStorage.setItem(SENT_KEY, JSON.stringify({ to, at: Date.now() - agoMs, shown: to }));
}

const sendButton = (email = EMAIL) =>
  screen.queryByRole<HTMLButtonElement>("button", { name: ru("emailBanner.send", { email }) });
const codeField = () => screen.queryByLabelText(ru("emailBanner.codePlaceholder"));

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem(STORAGE_KEY, "ru");
  user = me();
  canVerify = true;
  emailAuthEnabled = true;
  request.mockReset();
  confirm.mockReset();
  refreshMe.mockReset();
  refreshMe.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("плашка неподтверждённой почты: кому показывать", () => {
  it("почта не подтверждена, код ещё не просили — просьба и кнопка «Прислать код на адрес», поля нет", () => {
    renderBanner();
    expect(screen.getByText(ru("emailBanner.askTitle", { email: EMAIL }))).toBeTruthy();
    expect(sendButton()).toBeTruthy();
    // Не просим ввести код из письма, которое никто не отправлял.
    expect(codeField()).toBeNull();
    expect(screen.queryByText(ru("emailBanner.text", { email: EMAIL }))).toBeNull();
    expect(screen.queryByText(ru("emailBanner.spam"))).toBeNull();
    // Код сам не отправляем: плашка видна на каждом заходе на Главную.
    expect(request).not.toHaveBeenCalled();
  });

  it("смена почты — адрес берётся из pending_email: код уйдёт туда", () => {
    user = me({ pending_email: "new@example.org" });
    renderBanner();
    expect(screen.getByText(ru("emailBanner.askTitle", { email: "new@example.org" }))).toBeTruthy();
    expect(sendButton("new@example.org")).toBeTruthy();
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

  it("вход по почте выключен на установке — плашки нет", () => {
    emailAuthEnabled = false;
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });

  it("не вошёл — плашки нет", () => {
    user = null;
    const { container } = renderBanner();
    expect(container.textContent).toBe("");
  });
});

describe("плашка неподтверждённой почты: первая отправка кода", () => {
  it("прислали — появляются поле кода, «пришло на …» и «Спам»; повтор спит", async () => {
    request.mockResolvedValue({ success: true, target_email: EMAIL, expires_at: "" });
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(codeField()).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.text", { email: EMAIL }))).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.spam"))).toBeTruthy();
    expect(sendButton()).toBeNull();
    // Первая отправка — не «новый код»: о ней и так говорит появившееся поле.
    expect(screen.queryByText(ru("emailBanner.sent", { email: EMAIL }))).toBeNull();
    const again = screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S })).closest("button")!;
    expect(again.disabled).toBe(true);
  });

  it("«пришло на» — адрес из ответа бэкенда", async () => {
    user = me({ pending_email: "new@example.org" });
    request.mockResolvedValue({ success: true, target_email: "New@Example.org", expires_at: "" });
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton("new@example.org")!);
    });
    expect(screen.getByText(ru("emailBanner.text", { email: "New@Example.org" }))).toBeTruthy();
  });

  it("перезагрузка после отправки не прячет поле и не сбрасывает паузу", async () => {
    vi.useFakeTimers();
    request.mockResolvedValue({ success: true, target_email: EMAIL, expires_at: "" });
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    cleanup();

    renderBanner();
    expect(codeField()).toBeTruthy();
    expect(sendButton()).toBeNull();
    const again = screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S - 20 })).closest("button")!;
    expect(again.disabled).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("код просили для другого адреса (почту сменили) — снова предлагаем прислать", () => {
    markSent("old@example.org");
    renderBanner();
    expect(sendButton()).toBeTruthy();
    expect(codeField()).toBeNull();
  });

  it("sessionStorage недоступен — плашка всё равно работает", async () => {
    // Приватный режим / запрет сайта: любое обращение к хранилищу бросает.
    const blocked = () => {
      throw new Error("SecurityError");
    };
    vi.stubGlobal("sessionStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
    request.mockResolvedValue({ success: true, target_email: EMAIL, expires_at: "" });
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    expect(codeField()).toBeTruthy();
  });

  it("503 — почта на установке выключена: плашка уходит и не возвращается до конца сессии", async () => {
    request.mockRejectedValue(new ApiError(503, "Почта не настроена"));
    const { container } = renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    expect(container.textContent).toBe("");
    expect(sessionStorage.getItem(OFF_KEY)).toBe("1");
    cleanup();

    const again = renderBanner();
    expect(again.container.textContent).toBe("");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("другая ошибка отправки — сообщение, поле не появляется, повторить можно сразу", async () => {
    request.mockRejectedValue(new ApiError(500, "Не удалось отправить письмо"));
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    expect(screen.getByText("Не удалось отправить письмо")).toBeTruthy();
    expect(codeField()).toBeNull();
    expect(sendButton()!.disabled).toBe(false);
  });

  it("рейт-лимит (429) на первой отправке — ответ и пауза на кнопке", async () => {
    vi.useFakeTimers();
    request.mockRejectedValue(new ApiError(429, "Слишком много попыток. Повторите через минуту."));
    renderBanner();
    await act(async () => {
      fireEvent.click(sendButton()!);
    });
    expect(screen.getByText("Слишком много попыток. Повторите через минуту.")).toBeTruthy();
    expect(codeField()).toBeNull();
    expect(screen.getByText(ru("emailBanner.resendIn", { s: RESEND_PAUSE_S })).closest("button")!.disabled).toBe(true);
  });
});

describe("плашка неподтверждённой почты: подтверждение", () => {
  beforeEach(() => markSent());

  it("верный код — существующая ручка подтверждения, профиль перечитан, плашка исчезла", async () => {
    confirm.mockResolvedValue({ success: true, email: EMAIL });
    const { container } = renderBanner();
    fireEvent.change(codeField()!, { target: { value: " 123456 " } });
    await act(async () => {
      fireEvent.click(screen.getByText(ru("set.confirm")));
    });
    expect(confirm).toHaveBeenCalledWith({ code: "123456" });
    expect(refreshMe).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("");
    expect(sessionStorage.getItem(SENT_KEY)).toBeNull();
  });

  it("неверный код — текст ошибки бэкенда, плашка остаётся", async () => {
    confirm.mockRejectedValue(new ApiError(400, "Неверный код"));
    renderBanner();
    fireEvent.change(codeField()!, { target: { value: "000000" } });
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
  beforeEach(() => markSent());

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
    // Поле для кода остаётся: код, присланный раньше, ещё можно ввести.
    expect(codeField()).toBeTruthy();
  });

  it("другая ошибка отправки — сообщение без паузы: повторить можно сразу", async () => {
    request.mockRejectedValue(new ApiError(500, "Не удалось отправить письмо"));
    renderBanner();
    await act(async () => {
      fireEvent.click(screen.getByText(ru("emailBanner.resend")));
    });
    expect(screen.getByText("Не удалось отправить письмо")).toBeTruthy();
    expect(screen.getByText(ru("emailBanner.resend")).closest("button")!.disabled).toBe(false);
  });

  it("503 при повторе — плашка тоже уходит", async () => {
    request.mockRejectedValue(new ApiError(503, "Почта не настроена"));
    const { container } = renderBanner();
    await act(async () => {
      fireEvent.click(screen.getByText(ru("emailBanner.resend")));
    });
    expect(container.textContent).toBe("");
  });
});
