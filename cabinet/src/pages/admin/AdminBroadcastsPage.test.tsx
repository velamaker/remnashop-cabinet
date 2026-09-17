import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import type { AdminBroadcast } from "@/api/admin";

// Сегмент «Истекают скоро» в форме рассылки. Заперто:
//  • бэкенд не умеет сегмент (нет ключа в счётчиках: чужой бот или не встала
//    правка рассылок) — пункта нет вовсе;
//  • смена дней перезапрашивает счётчик и уходит в отправку;
//  • вместе с «Все», «С подпиской» и «По тарифу» его не выбрать — часть людей
//    получила бы сообщение дважды;
//  • история подписывает такую рассылку с числом дней.
const baseCounts = {
  TG_ALL: 10,
  TG_PLAN: 0,
  TG_SUBSCRIBED: 6,
  TG_UNSUBSCRIBED: 4,
  TG_TRIAL: 1,
  TG_EXPIRED: 2,
  EMAIL_ALL: 3,
};

const audienceCounts = vi.fn();
const create = vi.fn();
let history: AdminBroadcast[] = [];

vi.mock("@/api/admin", () => ({
  broadcastsAdminApi: {
    list: () => Promise.resolve({ items: history, total: history.length }),
    get: () => Promise.reject(new Error("not used")),
    audienceCounts: (planId?: number, expiringDays?: number) => audienceCounts(planId, expiringDays),
    create: (...args: unknown[]) => create(...args),
  },
  plansAdminApi: { list: () => Promise.resolve({ items: [] }) },
}));

const { default: AdminBroadcastsPage } = await import("./AdminBroadcastsPage");

const expiringButton = () =>
  screen.queryByRole("button", { name: /заканчивается в ближайшие N дней/ });

beforeEach(() => {
  history = [];
  audienceCounts.mockReset();
  create.mockReset();
  create.mockResolvedValue({ telegram: ["t1"], email: [] });
});
afterEach(cleanup);

describe("Рассылки: «Истекают скоро»", () => {
  it("нет ключа в счётчиках — пункта нет; есть — пункт на месте", async () => {
    audienceCounts.mockResolvedValue(baseCounts);
    render(<AdminBroadcastsPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: /все зарегистрированные в боте/ })).toHaveTextContent("10"));
    expect(expiringButton()).toBeNull();
    cleanup();

    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    render(<AdminBroadcastsPage />);
    await waitFor(() => expect(expiringButton()).not.toBeNull());
    expect(expiringButton()).toHaveTextContent("5");
  });

  it("смена дней перезапрашивает счётчик, отправка передаёт дни", async () => {
    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    render(<AdminBroadcastsPage />);
    await waitFor(() => expect(expiringButton()).not.toBeNull());

    fireEvent.change(screen.getByPlaceholderText("Текст сообщения…"), { target: { value: "Продлите подписку" } });
    fireEvent.click(expiringButton()!);
    fireEvent.change(screen.getByLabelText("Истекают в ближайшие"), { target: { value: "14" } });
    await waitFor(() => expect(audienceCounts).toHaveBeenLastCalledWith(undefined, 14));

    fireEvent.click(screen.getByRole("button", { name: /Отправить/ }));
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Точно отправить/ }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith("Продлите подписку", ["TG_EXPIRING"], undefined, 14);
  });

  it("вместе с «Все», «С подпиской» и «По тарифу» не выбирается", async () => {
    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    render(<AdminBroadcastsPage />);
    await waitFor(() => expect(expiringButton()).not.toBeNull());

    fireEvent.click(expiringButton()!);
    expect(screen.getByRole("button", { name: /все зарегистрированные в боте/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /активная \(вкл\. пробные\)/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /активные на выбранном тарифе/ })).toBeDisabled();
    // Непересекающиеся сегменты остаются доступны.
    // (у «Пробного периода» одна подсказка на Telegram и Email — берём первый, Telegram).
    expect(screen.getAllByRole("button", { name: /сейчас на триале/ })[0]).not.toBeDisabled();

    // И в обратную сторону: выбран «С подпиской» — «Истекают скоро» заблокирован.
    fireEvent.click(expiringButton()!);
    fireEvent.click(screen.getByRole("button", { name: /активная \(вкл\. пробные\)/ }));
    expect(expiringButton()).toBeDisabled();
  });

  it("история подписывает рассылку с числом дней", async () => {
    audienceCounts.mockResolvedValue(baseCounts);
    history = [
      {
        task_id: "b1",
        status: "COMPLETED",
        audience: "TG_EXPIRING",
        expiring_days: 7,
        total_count: 4,
        success_count: 4,
        failed_count: 0,
        created_at: null,
      },
    ];
    render(<AdminBroadcastsPage />);
    await waitFor(() => expect(screen.getByText("Telegram · истекают скоро (7 дней)")).toBeInTheDocument());
  });
});
