import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent, within } from "@testing-library/react";
import { ApiError } from "@/types/api";
import type { DigestEmailStatus } from "@/api/admin";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// Карточка «Сводка письмом» на странице дайджеста. Заперто:
//  • у кабинета поверх чужого бота ручки нет (501/404) — карточки нет, а сам
//    дайджест на месте;
//  • сохранение шлёт только изменённые поля, отказ включить (409) виден дословно;
//  • тестовое письмо не уходит без адреса и уходит ровно на введённый.
const status = (over: Partial<DigestEmailStatus> = {}): DigestEmailStatus => ({
  email_enabled: false,
  email_from: "",
  effective_from: "noreply@example.test",
  needs_separate_sender: true,
  digest_enabled: true,
  day_of_month: 1,
  hour: 12,
  audience: 6,
  opted_out: 0,
  max_per_run: 200,
  blockers: [],
  last: null,
  ...over,
});

let getEmail: () => Promise<DigestEmailStatus> = () => Promise.resolve(status());
const update = vi.fn();
const test = vi.fn();

vi.mock("@/api/admin", () => ({
  digestAdminApi: {
    get: () => Promise.resolve({ enabled: true, day_of_month: 1, hour: 12 }),
    update: (data: unknown) => Promise.resolve(data),
  },
  digestEmailAdminApi: {
    get: () => getEmail(),
    update: (data: unknown) => update(data),
    preview: () => Promise.resolve({ subject: "Ваш месяц с Test", text: "", html: "<p>x</p>" }),
    dryRun: () => Promise.resolve({ audience: 0, examined: 0, truncated: false, would_send: 0, blockers: [], items: [] }),
    test: (to: string) => test(to),
  },
}));

const { default: AdminDigestPage } = await import("./AdminDigestPage");

// Карточка настроек больше не хранит русский текст в коде: подписи приходят из
// словаря по ключам adm.settings.*. Тест сверяется с тем же словарём (и держит
// кабинет на русском), иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminDigestPage />
    </I18nProvider>,
  );

const digestCard = () => screen.queryByText(ru("adm.settings.digest_title"));
const emailCardTitle = () => screen.queryByText(ru("adm.settings.dm_title"));
const emailCard = () => within(emailCardTitle()!.closest("section")!);

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  getEmail = () => Promise.resolve(status());
  update.mockReset();
  test.mockReset();
});
afterEach(cleanup);

describe("Сводка письмом: карточка", () => {
  it.each([501, 404])("ручки нет (%i) → карточки нет, дайджест на месте", async (code) => {
    getEmail = () => Promise.reject(new ApiError(code, "нет соответствия"));
    renderPage();

    await waitFor(() => expect(digestCard()).not.toBeNull());
    // Дожидаемся, пока отказ точно пришёл, и только потом проверяем отсутствие.
    await new Promise((r) => setTimeout(r, 0));
    expect(emailCardTitle()).toBeNull();
    expect(screen.queryByText(ru("adm.settings.load_failed"))).toBeNull();
  });

  it("сохранение шлёт только изменённое; 409 показывает причину", async () => {
    update.mockImplementationOnce((data: Record<string, unknown>) =>
      Promise.resolve(status({ email_from: String(data.email_from) })),
    );
    renderPage();
    await waitFor(() => expect(emailCardTitle()).not.toBeNull());

    const card = emailCard();
    // Подпись поля — отдельный <label> рядом с input (так устроен общий Field).
    const fromInput = card.getByText(rx("adm.settings.dm_from_brevo")).parentElement!.querySelector("input")!;
    fireEvent.change(fromInput, { target: { value: " digest@example.test " } });
    fireEvent.click(card.getByRole("button", { name: rx("adm.settings.save") }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith({ email_from: "digest@example.test" });

    const reason = "Не задан адрес кабинета (WEB_CABINET_URL) — без ссылки «Отписаться» письма не отправляются.";
    update.mockRejectedValueOnce(new ApiError(409, reason));
    fireEvent.click(card.getByRole("button", { name: rx("adm.settings.dm_send_enable") }));
    fireEvent.click(card.getByRole("button", { name: rx("adm.settings.save") }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update).toHaveBeenLastCalledWith({ email_enabled: true });
    await waitFor(() => expect(card.getByText(reason)).toBeInTheDocument());
  });

  it("тест без адреса не уходит; с адресом — ровно на него", async () => {
    test.mockResolvedValue({ success: true, to: "me@example.test", from: "digest@example.test" });
    renderPage();
    await waitFor(() => expect(emailCardTitle()).not.toBeNull());

    const card = emailCard();
    const send = card.getByRole("button", { name: ru("adm.settings.dm_test_btn") });
    fireEvent.click(send);
    expect(test).not.toHaveBeenCalled();

    fireEvent.change(card.getByPlaceholderText("you@example.com"), { target: { value: "me@example.test" } });
    fireEvent.click(send);
    await waitFor(() => expect(test).toHaveBeenCalledTimes(1));
    expect(test).toHaveBeenCalledWith("me@example.test");
    await waitFor(() =>
      expect(
        card.getByText(rx("adm.settings.dm_test_sent", { to: "me@example.test", from: "digest@example.test" })),
      ).toBeInTheDocument(),
    );
  });

  it("препятствия выводятся текстом; поле отправителя видно для Brevo", async () => {
    const blocker = "Почта не настроена или выключена — раздел «Почта».";
    getEmail = () =>
      Promise.resolve(
        status({
          blockers: [blocker],
          digest_enabled: false,
          last: { month: "2026-10", sent: 5, failed: 0, no_traffic: 1, usage_error: 0, over_limit: 0, provider_blocked: 0, sending: 1 },
        }),
      );
    renderPage();
    await waitFor(() => expect(emailCardTitle()).not.toBeNull());

    const card = emailCard();
    expect(card.getByText(blocker)).toBeInTheDocument();
    expect(card.getByText(ru("adm.settings.dm_digest_off"))).toBeInTheDocument();
    expect(card.getByText(ru("adm.settings.dm_from_brevo"))).toBeInTheDocument();
    expect(card.getByText(rx("adm.settings.dm_audience", { n: 6, out: 0 }))).toBeInTheDocument();
    expect(card.getByText(rx("adm.settings.dm_last_sending", { n: 1 }))).toBeInTheDocument();
  });
});
