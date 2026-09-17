import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, act } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { BulkDaysPreview, BulkJob, BulkMessagePreview } from "@/api/admin";

// «Массово по фильтру»: добавить дни и написать сообщение. Заперто то, что
// защищает людей от случайной массовой раздачи:
//  • бэкенд без этих ручек (501) — пункт прячется, запуска нет;
//  • «Далее» — только после предпросмотра, двойной клик не запускает дважды, повтор
//    после обрыва сети — тот же request_id, другие параметры — новый;
//  • предпросмотр честно говорит про резерв, паузу и исчерпанный трафик;
//  • повтор «за 24 часа» — только с явной галочкой;
//  • сообщение без каналов или длиннее 4000 символов не отправить, выключенная
//    почта видна заранее;
//  • кнопки управления задачами — только у полного доступа, опрос — только пока
//    что-то идёт;
//  • без полного доступа пунктов в списке действий нет вовсе.

const daysPreview = vi.fn();
const startDays = vi.fn();
const messagePreview = vi.fn();
const startMessage = vi.fn();
const testMessage = vi.fn();
const jobs = vi.fn();
const usersList = vi.fn();

vi.mock("@/api/admin", () => ({
  bulkJobsAdminApi: {
    daysPreview: (...a: unknown[]) => daysPreview(...a),
    startDays: (...a: unknown[]) => startDays(...a),
    messagePreview: (...a: unknown[]) => messagePreview(...a),
    startMessage: (...a: unknown[]) => startMessage(...a),
    testMessage: (...a: unknown[]) => testMessage(...a),
    jobs: (...a: unknown[]) => jobs(...a),
    items: () => Promise.resolve({ total: 0, items: [] }),
    cancel: () => Promise.resolve({}),
    resume: () => Promise.resolve({}),
  },
  usersAdminApi: { list: (...a: unknown[]) => usersList(...a) },
  subscriptionsAdminApi: {},
  plansAdminApi: {},
  grantsAdminApi: {},
}));

let auth = { isReadonlyAdmin: false, fullAccess: true, isOwner: true, canSection: () => true };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/contexts/BrandingContext", () => ({ useBranding: () => ({ can: () => true }) }));

const { BulkDaysDialog, BulkJobsPanel, BulkMessageDialog } = await import("./AdminUsersBulk");
const { default: AdminUsersPage } = await import("./AdminUsersPage");

const preview = (over: Partial<BulkDaysPreview> = {}): BulkDaysPreview => ({
  matched: 10,
  apply: 4,
  apply_frozen: 2,
  deferred: 0,
  skipped: { NO_SUBSCRIPTION: 2, RESERVE: 1, LIMITED: 1, EXPIRED: 0 },
  recently_extended: { count: 0, job_id: null, days: null },
  sample: [],
  segment_hash: "hash-1",
  active_job_id: null,
  limits: { max_days: 365, max_users: 5000 },
  ...over,
});

const messagePreviewData = (over: Partial<BulkMessagePreview> = {}): BulkMessagePreview => ({
  matched: 5,
  recipients: 5,
  skipped: { BLOCKED: 0, STAFF: 0 },
  by_channel: { telegram: 3, telegram_bot_blocked: 1, push_only: 0, email_only: 1, cabinet_only: 1, unreachable: 0 },
  email_enabled: true,
  segment_hash: "m-hash",
  active_job_id: null,
  ...over,
});

const job = (over: Partial<BulkJob> = {}): BulkJob => ({
  id: 7,
  kind: "days",
  status: "COMPLETED",
  created_by: "@owner",
  created_at: "2026-09-17T10:00:00Z",
  started_at: null,
  finished_at: null,
  total: 10,
  done: 10,
  applied: 5,
  skipped: 5,
  failed: 0,
  unknown: 0,
  verify_flagged: 0,
  params: { days: 3, include_trial: false, include_limited: false, channels: null, text_preview: null },
  pause_reason: null,
  parent_job_id: null,
  child_job_id: null,
  breakdown: {},
  ...over,
});

const noop = () => {};

beforeEach(() => {
  for (const fn of [daysPreview, startDays, messagePreview, startMessage, testMessage, jobs, usersList]) fn.mockReset();
  auth = { isReadonlyAdmin: false, fullAccess: true, isOwner: true, canSection: () => true };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const nextButton = () => screen.getByRole("button", { name: "Далее" });

describe("«Добавить дни подписки»", () => {
  it("бэкенд не умеет (501) — пункт прячется, запуска нет", async () => {
    daysPreview.mockRejectedValue(new ApiError(501, "Не реализовано"));
    const onUnsupported = vi.fn();
    render(<BulkDaysDialog filters={{}} onClose={noop} onStarted={noop} onUnsupported={onUnsupported} />);
    await waitFor(() => expect(onUnsupported).toHaveBeenCalledWith("users.bulk.days"));
    expect(startDays).not.toHaveBeenCalled();
  });

  it("«Далее» ждёт предпросмотра; двойной клик — один запуск; повтор после сбоя — тот же request_id", async () => {
    let resolve: (p: BulkDaysPreview) => void = noop;
    daysPreview.mockImplementationOnce(() => new Promise((r) => (resolve = r)));
    const onStarted = vi.fn();
    render(<BulkDaysDialog filters={{ search: "a@b" }} onClose={noop} onStarted={onStarted} onUnsupported={noop} />);
    expect(nextButton()).toBeDisabled();
    await waitFor(() => expect(daysPreview).toHaveBeenCalledTimes(1));
    expect(daysPreview.mock.calls[0]?.[0]).toEqual({ search: "a@b" });
    act(() => resolve(preview()));
    await waitFor(() => expect(nextButton()).toBeEnabled());

    fireEvent.click(nextButton());
    expect(screen.getByText("Добавить 3 дня 6 подпискам?")).toBeInTheDocument();
    startDays.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({ job_id: 12, status: "QUEUED", total: 10, apply: 6, duplicate: false });
    const yes = screen.getByRole("button", { name: "Да, добавить" });
    fireEvent.click(yes);
    fireEvent.click(yes);
    await waitFor(() => expect(screen.getByText("Не удалось запустить — попробуйте ещё раз")).toBeInTheDocument());
    expect(startDays).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Да, добавить" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(12));
    const first = startDays.mock.calls[0]?.[1];
    const second = startDays.mock.calls[1]?.[1];
    expect(second.request_id).toBe(first.request_id);
    expect(second).toMatchObject({ days: 3, segment_hash: "hash-1", expected_apply: 6, notify: null, allow_repeat: false });

    // Сменили дни — это уже другой запуск.
    daysPreview.mockResolvedValue(preview());
    fireEvent.click(screen.getByRole("button", { name: "Назад" }));
    fireEvent.change(screen.getByLabelText("Сколько дней добавить"), { target: { value: "5" } });
    await waitFor(() => expect(daysPreview).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(nextButton()).toBeEnabled());
    fireEvent.click(nextButton());
    startDays.mockResolvedValueOnce({ job_id: 13, status: "QUEUED", total: 10, apply: 6, duplicate: false });
    fireEvent.click(screen.getByRole("button", { name: "Да, добавить" }));
    await waitFor(() => expect(startDays).toHaveBeenCalledTimes(3));
    expect(startDays.mock.calls[2]?.[1].request_id).not.toBe(first.request_id);
    expect(startDays.mock.calls[2]?.[1].days).toBe(5);
  });

  it("предпросмотр говорит про резерв, паузу и исчерпанный трафик", async () => {
    daysPreview.mockResolvedValue(preview());
    render(<BulkDaysDialog filters={{}} onClose={noop} onStarted={noop} onUnsupported={noop} />);
    await waitFor(() => expect(screen.getByText("на резервном доступе (не оплачено) — 1")).toBeInTheDocument());
    expect(screen.getByText("Из них на паузе: 2 — дни добавятся к остатку паузы")).toBeInTheDocument();
    expect(screen.getByText(/^исчерпан трафик — 1/)).toBeInTheDocument();
    expect(screen.queryByText(/подписка истекла/)).toBeNull();
  });

  it("повтор за 24 часа — только с галочкой «Понимаю»", async () => {
    daysPreview.mockResolvedValue(preview({ recently_extended: { count: 3, job_id: 9, days: 2 } }));
    startDays.mockResolvedValue({ job_id: 14, status: "QUEUED", total: 10, apply: 6, duplicate: false });
    render(<BulkDaysDialog filters={{}} onClose={noop} onStarted={noop} onUnsupported={noop} />);
    await waitFor(() => expect(nextButton()).toBeEnabled());
    fireEvent.click(nextButton());
    expect(screen.getByText(/уже добавляли дни за последние 24 часа: 3 \(задача №9, \+2 дн\.\)/)).toBeInTheDocument();
    const yes = screen.getByRole("button", { name: "Да, добавить" });
    expect(yes).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Понимаю, добавить ещё раз"));
    expect(yes).toBeEnabled();
    fireEvent.click(yes);
    await waitFor(() => expect(startDays).toHaveBeenCalledTimes(1));
    expect(startDays.mock.calls[0]?.[1].allow_repeat).toBe(true);
  });
});

describe("«Сообщение отфильтрованным»", () => {
  it("без каналов или длиннее 4000 символов — не отправить; выключенная почта видна", async () => {
    messagePreview.mockResolvedValue(messagePreviewData({ email_enabled: false }));
    render(<BulkMessageDialog filters={{}} onClose={noop} onStarted={noop} onUnsupported={noop} />);
    await waitFor(() => expect(screen.getByText("Почта выключена в настройках — письма не уйдут")).toBeInTheDocument());
    const textarea = screen.getByLabelText("Текст сообщения");
    fireEvent.change(textarea, { target: { value: "Привет" } });
    await waitFor(() => expect(nextButton()).toBeEnabled());

    fireEvent.change(textarea, { target: { value: "я".repeat(4001) } });
    expect(nextButton()).toBeDisabled();
    expect(screen.getByText("4001/4000")).toBeInTheDocument();

    fireEvent.change(textarea, { target: { value: "Привет" } });
    for (const label of ["Telegram", /В ленту уведомлений кабинета/, /Письмом/]) {
      fireEvent.click(screen.getByLabelText(label));
    }
    await waitFor(() => expect(screen.getByText("Выберите хотя бы один канал")).toBeInTheDocument());
    expect(nextButton()).toBeDisabled();
    expect(startMessage).not.toHaveBeenCalled();
  });

  it("«Проверить на себе» говорит, что пришло", async () => {
    messagePreview.mockResolvedValue(messagePreviewData());
    testMessage.mockResolvedValueOnce({ telegram: false, reason: "no_telegram" });
    render(<BulkMessageDialog filters={{}} onClose={noop} onStarted={noop} onUnsupported={noop} />);
    fireEvent.change(screen.getByLabelText("Текст сообщения"), { target: { value: "<b>Привет</b>" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить на себе" }));
    await waitFor(() =>
      expect(screen.getByText("У вашего аккаунта нет Telegram — проверить отправку нельзя")).toBeInTheDocument(),
    );
    expect(testMessage).toHaveBeenCalledWith("<b>Привет</b>");
  });
});

describe("«Фоновые задачи»", () => {
  it("без полного доступа или read-only — ни «Остановить», ни «Продолжить»", async () => {
    jobs.mockResolvedValue({ items: [job({ status: "PAUSED", pause_reason: "Панель VPN не отвечает" })], active: { days: 7, message: null } });
    render(<BulkJobsPanel fullAccess={false} readonly={false} refreshKey={0} onWriteRecipients={noop} onUnsupported={noop} />);
    await waitFor(() => expect(screen.getByText("Панель VPN не отвечает")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Остановить" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Продолжить" })).toBeNull();
    cleanup();

    render(<BulkJobsPanel fullAccess readonly refreshKey={0} onWriteRecipients={noop} onUnsupported={noop} />);
    await waitFor(() => expect(screen.getByText("Панель VPN не отвечает")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Остановить" })).toBeNull();
    cleanup();

    render(<BulkJobsPanel fullAccess readonly={false} refreshKey={0} onWriteRecipients={noop} onUnsupported={noop} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Продолжить" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Остановить" })).toBeInTheDocument();
  });

  it("опрос идёт, только пока есть идущая задача", async () => {
    vi.useFakeTimers();
    jobs.mockResolvedValue({ items: [job({ status: "COMPLETED" })], active: { days: null, message: null } });
    render(<BulkJobsPanel fullAccess readonly={false} refreshKey={0} onWriteRecipients={noop} onUnsupported={noop} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(jobs).toHaveBeenCalledTimes(1);
    cleanup();

    jobs.mockReset();
    jobs.mockResolvedValueOnce({ items: [job({ status: "PROCESSING", done: 3 })], active: { days: 7, message: null } });
    jobs.mockResolvedValue({ items: [job({ status: "COMPLETED" })], active: { days: null, message: null } });
    render(<BulkJobsPanel fullAccess readonly={false} refreshKey={0} onWriteRecipients={noop} onUnsupported={noop} />);
    // Сначала доезжает первый ответ и перерисовка — только тогда заводится таймер опроса.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_100);
    });
    expect(jobs).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(jobs).toHaveBeenCalledTimes(2);
  });

  it("501 на списке задач — панели нет", async () => {
    jobs.mockRejectedValue(new ApiError(501, "Не реализовано"));
    const onUnsupported = vi.fn();
    render(<BulkJobsPanel fullAccess readonly={false} refreshKey={0} onWriteRecipients={noop} onUnsupported={onUnsupported} />);
    await waitFor(() => expect(onUnsupported).toHaveBeenCalledWith("users.bulk.jobs"));
    expect(screen.queryByText("Фоновые задачи")).toBeNull();
  });
});

describe("«Пользователи»: пункты массовых задач", () => {
  const options = () => Array.from(screen.getByLabelText("Массовое действие").querySelectorAll("option")).map((o) => o.textContent);

  it("без полного доступа пунктов нет, с полным — есть", async () => {
    usersList.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
    jobs.mockResolvedValue({ items: [], active: { days: null, message: null } });
    auth = { ...auth, fullAccess: false };
    render(<AdminUsersPage />);
    await waitFor(() => expect(usersList).toHaveBeenCalled());
    expect(options()).not.toContain("Добавить дни подписки…");
    expect(options()).not.toContain("Написать сообщение…");
    cleanup();

    auth = { ...auth, fullAccess: true };
    render(<AdminUsersPage />);
    await waitFor(() => expect(options()).toContain("Добавить дни подписки…"));
    expect(options()).toContain("Написать сообщение…");
  });
});
