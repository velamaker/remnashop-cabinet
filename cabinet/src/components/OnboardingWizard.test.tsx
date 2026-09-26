import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Мастер подключения: последний шаг «проверим, что заработало».
 *
 * ЗАЧЕМ ЗАПЕРТО. Из 200 пробных аккаунтов 169 не подключились ни разу: мастер
 * отпускал человека словом «Готово» раньше, чем VPN реально заработал. Шаг сам
 * спрашивает подписку (каждые 5 с, до 2 минут) и либо подтверждает соединение,
 * либо ведёт в самодиагностику, а без данных панели — нейтрально завершается.
 * Здесь — все исходы и главное обещание: опрос
 * не переживает размонтирование (иначе кабинет ходил бы в панель бесконечно).
 */

const current = vi.fn();
vi.mock("@/api/subscription", () => ({ subscriptionApi: { current: () => current() } }));

const { OnboardingWizard, ConnectionCheck, CONNECTION_POLL_MS, CONNECTION_WAIT_MS } = await import(
  "./OnboardingWizard"
);

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

function sub(over: Record<string, unknown> = {}) {
  return {
    status: "ACTIVE",
    url: "https://sub.example/abc",
    used_traffic_bytes: 0,
    lifetime_used_traffic_bytes: 0,
    online_at: null,
    ...over,
  };
}
const NEVER = sub();
const ONLINE = sub({ online_at: "2026-09-26T10:00:00Z" });

/** Прогнать таймеры и дождаться ответов «панели» внутри act. */
const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

function renderCheck(onDone = vi.fn()) {
  return render(
    <MemoryRouter>
      <I18nProvider>
        <ConnectionCheck onDone={onDone} />
      </I18nProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  localStorage.setItem(STORAGE_KEY, "ru");
  current.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("мастер подключения: ждём первое подключение", () => {
  it("подключение появилось во время ожидания — «соединение есть», опрос остановлен", async () => {
    current.mockResolvedValueOnce(NEVER).mockResolvedValueOnce(NEVER).mockResolvedValue(ONLINE);
    renderCheck();
    await tick(0);
    expect(screen.getByText(ru("onb.check.waiting"))).toBeTruthy();
    expect(current).toHaveBeenCalledTimes(1);

    await tick(CONNECTION_POLL_MS);
    expect(current).toHaveBeenCalledTimes(2);
    expect(screen.getByText(ru("onb.check.waiting"))).toBeTruthy();

    await tick(CONNECTION_POLL_MS);
    expect(current).toHaveBeenCalledTimes(3);
    expect(screen.getByText(ru("onb.check.ok"))).toBeTruthy();
    // Это новое подключение, а не «уже было раньше».
    expect(screen.queryByText(ru("onb.check.already"))).toBeNull();

    await tick(CONNECTION_WAIT_MS);
    expect(current).toHaveBeenCalledTimes(3);
  });

  it("расход трафика без online_at — тоже подключение (панель заполняет поля не разом)", async () => {
    current
      .mockResolvedValueOnce(NEVER)
      .mockResolvedValue(sub({ used_traffic_bytes: 1024, lifetime_used_traffic_bytes: 1024 }));
    renderCheck();
    await tick(0);
    await tick(CONNECTION_POLL_MS);
    expect(screen.getByText(ru("onb.check.ok"))).toBeTruthy();
  });

  it("2 минуты без подключения — «пока не видим», путь в самодиагностику и повтор", async () => {
    current.mockResolvedValue(NEVER);
    renderCheck();
    await tick(0);
    await tick(CONNECTION_WAIT_MS - CONNECTION_POLL_MS);
    // За секунду до конца ещё ждём.
    expect(screen.queryByText(ru("onb.check.notYet"))).toBeNull();

    await tick(CONNECTION_POLL_MS);
    expect(screen.getByText(ru("onb.check.notYet"))).toBeTruthy();
    const calls = current.mock.calls.length;
    // Первый вопрос сразу + раз в 5 с до 2 минут.
    expect(calls).toBe(1 + CONNECTION_WAIT_MS / CONNECTION_POLL_MS);
    const diag = screen.getByText(ru("onb.check.toDiag")).closest("a");
    expect(diag?.getAttribute("href")).toBe("/support");

    // После тайм-аута в панель больше не ходим.
    await tick(CONNECTION_WAIT_MS);
    expect(current).toHaveBeenCalledTimes(calls);

    // «Проверить ещё раз» — новый круг; подключение на нём — «соединение есть»,
    // а не «уже было»: мы-то знаем, что минуту назад его не было.
    current.mockResolvedValue(ONLINE);
    fireEvent.click(screen.getByText(ru("onb.check.again")));
    await tick(0);
    expect(current).toHaveBeenCalledTimes(calls + 1);
    expect(screen.getByText(ru("onb.check.ok"))).toBeTruthy();
  });

  // ПАНЕЛЬ МОЛЧИТ — НЕ ПОВОД ВЫНОСИТЬ ВЕРДИКТ (то же правило, что в самодиагностике).
  // Все признаки подключения пустые: адаптер «Бедолаги» их не отдаёт вовсе, наш бэкенд —
  // когда Remnawave не ответила. «Пока не видим подключения» тут было бы неправдой.
  it("в ответе нет ни одного признака подключения — нейтральное «Готово», без вердикта", async () => {
    current.mockResolvedValue(
      sub({ online_at: null, used_traffic_bytes: null, lifetime_used_traffic_bytes: null }),
    );
    const onDone = vi.fn();
    renderCheck(onDone);
    await tick(0);
    expect(screen.getByText(ru("onb.check.unknown"))).toBeTruthy();
    expect(screen.queryByText(ru("onb.check.waiting"))).toBeNull();
    expect(screen.queryByText(ru("onb.check.ok"))).toBeNull();
    expect(screen.queryByText(ru("onb.check.already"))).toBeNull();

    // Ждать нечего: опрос не продолжается, и через 2 минуты вердикта тоже нет.
    await tick(CONNECTION_WAIT_MS * 2);
    expect(current).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(ru("onb.check.notYet"))).toBeNull();

    fireEvent.click(screen.getByText(ru("onb.done")));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("поля отсутствуют в ответе вовсе (чужой бэкенд) — тоже без вердикта", async () => {
    current.mockResolvedValue({ status: "ACTIVE", url: "https://sub.example/abc" });
    renderCheck();
    await tick(0);
    expect(screen.getByText(ru("onb.check.unknown"))).toBeTruthy();
    await tick(CONNECTION_WAIT_MS);
    expect(screen.queryByText(ru("onb.check.notYet"))).toBeNull();
  });

  it("панель замолчала посреди ожидания — вердикта нет и после двух минут", async () => {
    current
      .mockResolvedValueOnce(NEVER)
      .mockResolvedValue(sub({ online_at: null, used_traffic_bytes: null, lifetime_used_traffic_bytes: null }));
    renderCheck();
    await tick(0);
    expect(screen.getByText(ru("onb.check.waiting"))).toBeTruthy();
    await tick(CONNECTION_POLL_MS);
    expect(screen.getByText(ru("onb.check.unknown"))).toBeTruthy();
    await tick(CONNECTION_WAIT_MS);
    expect(current).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(ru("onb.check.notYet"))).toBeNull();
  });

  it("нули — это данные, а не молчание: без подключения через 2 минуты «пока не видим»", async () => {
    // Хоть одно поле пришло (пусть нулём) — панель ответила, вердикт честный.
    current.mockResolvedValue(sub({ online_at: null, used_traffic_bytes: 0, lifetime_used_traffic_bytes: null }));
    renderCheck();
    await tick(0);
    expect(screen.queryByText(ru("onb.check.unknown"))).toBeNull();
    await tick(CONNECTION_WAIT_MS);
    expect(screen.getByText(ru("onb.check.notYet"))).toBeTruthy();
  });

  it("сбой запроса не обрывает ожидание — спрашиваем дальше", async () => {
    current.mockRejectedValueOnce(new Error("network")).mockResolvedValue(ONLINE);
    renderCheck();
    await tick(0);
    expect(screen.getByText(ru("onb.check.waiting"))).toBeTruthy();
    await tick(CONNECTION_POLL_MS);
    expect(screen.getByText(ru("onb.check.ok"))).toBeTruthy();
  });

  it("человек уже подключался раньше — сразу «уже работает», без ожидания", async () => {
    current.mockResolvedValue(ONLINE);
    const onDone = vi.fn();
    renderCheck(onDone);
    await tick(0);
    expect(screen.getByText(ru("onb.check.already"))).toBeTruthy();
    expect(screen.queryByText(ru("onb.check.waiting"))).toBeNull();

    await tick(CONNECTION_WAIT_MS);
    expect(current).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText(ru("onb.done")));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("размонтирование между вопросами гасит таймер опроса", async () => {
    current.mockResolvedValue(NEVER);
    const { unmount } = renderCheck();
    await tick(0);
    await tick(CONNECTION_POLL_MS);
    expect(current).toHaveBeenCalledTimes(2);

    unmount();
    await tick(CONNECTION_WAIT_MS * 2);
    expect(current).toHaveBeenCalledTimes(2);
  });

  it("ответ, долетевший после размонтирования, не заводит новый круг", async () => {
    let answer: (v: unknown) => void = () => {};
    current.mockResolvedValueOnce(NEVER).mockImplementationOnce(
      () => new Promise((resolve) => (answer = resolve)),
    );
    current.mockResolvedValue(NEVER);
    const { unmount } = renderCheck();
    await tick(0);
    await tick(CONNECTION_POLL_MS);
    expect(current).toHaveBeenCalledTimes(2);

    unmount();
    answer(NEVER); // запрос ушёл до размонтирования, ответ пришёл после
    await tick(CONNECTION_WAIT_MS * 2);
    expect(current).toHaveBeenCalledTimes(2);
  });

  it("весь мастер: шаг 4 после «Добавил», крестик на шаге 4 останавливает опрос", async () => {
    current.mockResolvedValue(NEVER);
    render(
      <MemoryRouter>
        <I18nProvider>
          <OnboardingWizard subUrl="https://sub.example/abc" />
        </I18nProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Android"));
    fireEvent.click(screen.getByText(ru("onb.installedNext")));
    // До шага 4 кабинет в панель не ходит.
    expect(current).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(ru("onb.addedNext")));
    await tick(0);
    expect(screen.getByText(ru("onb.step4"))).toBeTruthy();
    expect(current).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByLabelText(ru("onb.hide")));
    expect(screen.queryByText(ru("onb.step4"))).toBeNull();
    await tick(CONNECTION_WAIT_MS);
    expect(current).toHaveBeenCalledTimes(1);
  });
});
