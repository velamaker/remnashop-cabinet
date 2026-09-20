import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import type { AdminBroadcast } from "@/api/admin";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

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

// Страница больше не хранит русский текст в коде: подписи приходят из словаря по
// ключам adm.broadcasts.*. Тест сверяется с тем же словарём (и держит кабинет на
// русском), иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
/** Подпись кнопки — это ярлык + счётчик + подсказка, поэтому ищем по подстроке. */
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminBroadcastsPage />
    </I18nProvider>,
  );

const expiringButton = () =>
  screen.queryByRole("button", { name: rx("adm.broadcasts.hint_tg_expiring") });

beforeEach(() => {
  history = [];
  audienceCounts.mockReset();
  create.mockReset();
  create.mockResolvedValue({ telegram: ["t1"], email: [] });
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
});
afterEach(cleanup);

describe("Рассылки: «Истекают скоро»", () => {
  it("нет ключа в счётчиках — пункта нет; есть — пункт на месте", async () => {
    audienceCounts.mockResolvedValue(baseCounts);
    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: rx("adm.broadcasts.hint_tg_all") })).toHaveTextContent("10"));
    expect(expiringButton()).toBeNull();
    cleanup();

    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    renderPage();
    await waitFor(() => expect(expiringButton()).not.toBeNull());
    expect(expiringButton()).toHaveTextContent("5");
  });

  it("смена дней перезапрашивает счётчик, отправка передаёт дни", async () => {
    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    renderPage();
    await waitFor(() => expect(expiringButton()).not.toBeNull());

    fireEvent.change(screen.getByPlaceholderText(ru("adm.broadcasts.text_ph")), { target: { value: "Продлите подписку" } });
    fireEvent.click(expiringButton()!);
    fireEvent.change(screen.getByLabelText(ru("adm.broadcasts.expiring_days_label")), { target: { value: "14" } });
    await waitFor(() => expect(audienceCounts).toHaveBeenLastCalledWith(undefined, 14));

    fireEvent.click(screen.getByRole("button", { name: rx("adm.broadcasts.send") }));
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: rx("adm.broadcasts.confirm_send", { n: 5 }) }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith("Продлите подписку", ["TG_EXPIRING"], undefined, 14);
  });

  it("вместе с «Все», «С подпиской» и «По тарифу» не выбирается", async () => {
    audienceCounts.mockResolvedValue({ ...baseCounts, TG_EXPIRING: 5 });
    renderPage();
    await waitFor(() => expect(expiringButton()).not.toBeNull());

    fireEvent.click(expiringButton()!);
    expect(screen.getByRole("button", { name: rx("adm.broadcasts.hint_tg_all") })).toBeDisabled();
    expect(screen.getByRole("button", { name: rx("adm.broadcasts.hint_tg_subscribed") })).toBeDisabled();
    expect(screen.getByRole("button", { name: rx("adm.broadcasts.hint_tg_plan") })).toBeDisabled();
    // Непересекающиеся сегменты остаются доступны.
    // (у «Пробного периода» одна подсказка на Telegram и Email — берём первый, Telegram).
    expect(screen.getAllByRole("button", { name: rx("adm.broadcasts.hint_trial") })[0]).not.toBeDisabled();

    // И в обратную сторону: выбран «С подпиской» — «Истекают скоро» заблокирован.
    fireEvent.click(expiringButton()!);
    fireEvent.click(screen.getByRole("button", { name: rx("adm.broadcasts.hint_tg_subscribed") }));
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
    renderPage();
    const label = ru("adm.broadcasts.aud_tg_expiring_days", { days: ru("adm.broadcasts.days_many", { n: 7 }) });
    await waitFor(() => expect(screen.getByText(label)).toBeInTheDocument());
  });
});
