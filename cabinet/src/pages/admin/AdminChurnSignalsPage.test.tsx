import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ChurnSignalsAdminResponse } from "@/api/admin";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

/**
 * Страница «Сигналы до ухода». Запираем то, что владелец должен увидеть ДО
 * включения и после:
 *  * оба сигнала по умолчанию ВЫКЛЮЧЕНЫ — выкатка образа не должна сама начать писать
 *    людям;
 *  * доля «не работает» показывается от ОТВЕТИВШИХ, и из нуля ответов не получается
 *    «0 %» (это читалось бы как хорошая новость);
 *  * если последний проход не дождался панели, это видно громко — иначе «включил, а
 *    ничего не происходит» выглядит поломкой;
 *  * но при выключенных сигналах старый итог (и эта плашка) не выдаётся за текущий —
 *    страница говорит «выключено»; ошибки записи в базу видны, если были;
 *  * подпись «давно не подключался» не обещает «платящим» — правило шире;
 *  * сохранение отправляет ровно то, что в форме.
 */

const get = vi.fn();
const update = vi.fn();
vi.mock("@/api/admin", () => ({
  churnSignalsAdminApi: { get: () => get(), update: (d: unknown) => update(d) },
}));

const { AdminChurnSignalsPage } = await import("./AdminChurnSignalsPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminChurnSignalsPage />
    </I18nProvider>,
  );

const answer = (over: Partial<ChurnSignalsAdminResponse> = {}): ChurnSignalsAdminResponse => ({
  config: {
    check_enabled: false,
    check_delay_hours: 24,
    idle_enabled: false,
    idle_days: 7,
    idle_cooldown_days: 30,
    idle_min_days_left: 3,
  },
  check_window_hours: 24,
  idle_window_days: 3,
  stats: {
    days: 30,
    check_sent: 10,
    check_answered: 8,
    check_works: 6,
    check_broken: 2,
    check_failed: 0,
    check_answered_percent: 80,
    check_broken_percent: 25,
    idle_sent: 5,
    idle_returned: 2,
    idle_failed: 0,
    idle_returned_percent: 40,
  },
  broken: [{ user_id: 77, answered_at: "2026-09-25T10:00:00+00:00" }],
  optouts: { check: 1, idle: 2 },
  last_run: null,
  ...over,
});

const enabled = { ...answer().config, idle_enabled: true };

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  get.mockReset();
  update.mockReset();
  get.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("AdminChurnSignalsPage", () => {
  it("по умолчанию оба сигнала выключены", async () => {
    renderPage();
    const check = await screen.findByLabelText(rx("adm.churn.check_label"));
    const idle = screen.getByLabelText(rx("adm.churn.idle_label"));
    expect((check as HTMLInputElement).checked).toBe(false);
    expect((idle as HTMLInputElement).checked).toBe(false);
  });

  it("доля «не работает» — от ответивших, и кто именно ответил", async () => {
    renderPage();
    expect(await screen.findByText("2 · 25%")).toBeTruthy();
    expect(screen.getByText("8 · 80%")).toBeTruthy();
    expect(screen.getByText("ID 77")).toBeTruthy();
    expect(screen.getByText(rx("adm.churn.optouts", { check: 1, idle: 2 }))).toBeTruthy();
  });

  it("нет ответов — доля неизвестна, а не ноль", async () => {
    get.mockResolvedValue(
      answer({
        stats: { check_sent: 3, check_answered: 0, check_broken: 0, check_answered_percent: 0, check_broken_percent: null },
        broken: [],
      }),
    );
    renderPage();
    const tile = (await screen.findByText(ru("adm.churn.tile_broken"))).parentElement!;
    expect(tile.textContent).toContain("0 · —");
    expect(tile.textContent).not.toContain("0%");
    expect(screen.queryByText(ru("adm.churn.broken_list_title"))).toBeNull();
  });

  it("панель молчала на последнем проходе — предупреждение на виду", async () => {
    get.mockResolvedValue(
      answer({ config: enabled, last_run: { at: "2026-09-25T12:17:00+00:00", panel_ok: false } }),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Remnawave");
  });

  it("панель ответила — предупреждения нет, причины пропусков названы словами", async () => {
    get.mockResolvedValue(
      answer({
        config: enabled,
        last_run: {
          at: "2026-09-25T12:17:00+00:00",
          panel_ok: true,
          sent_check: 1,
          sent_idle: 0,
          failed: 0,
          skipped_check: { too_late: 12, frozen: 1 },
          skipped_idle: { recently_online: 30, no_panel_data: 2, panel_inactive: 4 },
        },
      }),
    );
    renderPage();
    await screen.findByText(ru("adm.churn.reason_too_late"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(ru("adm.churn.reason_recently_online"))).toBeTruthy();
    expect(screen.getByText(ru("adm.churn.reason_no_panel_data"))).toBeTruthy();
    expect(screen.getByText(ru("adm.churn.reason_frozen"))).toBeTruthy();
    expect(screen.getByText(ru("adm.churn.reason_panel_inactive"))).toBeTruthy();
    // Ошибок записи не было — и строки про них нет.
    expect(screen.queryByText(rx("adm.churn.last_run_errors", { errors: 0 }))).toBeNull();
  });

  it("оба сигнала выключены — «выключено» вместо старого итога и без красной плашки", async () => {
    get.mockResolvedValue(
      answer({
        last_run: {
          at: "2026-09-01T12:17:00+00:00",
          panel_ok: false,
          sent_check: 3,
          sent_idle: 1,
          failed: 0,
          errors: 2,
        },
      }),
    );
    renderPage();
    expect(await screen.findByText(ru("adm.churn.last_run_disabled"))).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // Строки итога нет вовсе: ни «спросили 3», ни даты прохода.
    expect(screen.queryByText(/спросили 3/)).toBeNull();
    expect(screen.queryByText(rx("adm.churn.last_run_errors", { errors: 2 }))).toBeNull();
  });

  it("галочку сняли, но не сохранили — крон ещё ходит, итог прохода остаётся", async () => {
    get.mockResolvedValue(
      answer({ config: enabled, last_run: { at: "2026-09-25T12:17:00+00:00", panel_ok: false } }),
    );
    renderPage();
    fireEvent.click(await screen.findByLabelText(rx("adm.churn.idle_label")));
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText(ru("adm.churn.last_run_disabled"))).toBeNull();
  });

  it("ошибки записи в базу видны, если были", async () => {
    get.mockResolvedValue(
      answer({
        config: enabled,
        last_run: { at: "2026-09-25T12:17:00+00:00", panel_ok: true, errors: 3 },
      }),
    );
    renderPage();
    expect(await screen.findByText(rx("adm.churn.last_run_errors", { errors: 3 }))).toBeTruthy();
  });

  it("подпись «давно не подключался» не обещает «платящим»: правило — непробная подписка", () => {
    for (const lang of ["ru", "en"] as const) {
      const label = translate("adm.churn.idle_label", undefined, lang);
      expect(label).not.toMatch(/платящ|paying/i);
      expect(label).toMatch(/непробн|non-trial/i);
    }
  });

  it("сохранение отправляет ровно то, что в форме", async () => {
    update.mockResolvedValue({ config: { ...answer().config, check_enabled: true, idle_days: 10 } });
    renderPage();
    fireEvent.click(await screen.findByLabelText(rx("adm.churn.check_label")));
    fireEvent.change(screen.getByLabelText(rx("adm.churn.idle_days_label")), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByText(ru("adm.churn.save")));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]![0]).toEqual({
      check_enabled: true,
      check_delay_hours: 24,
      idle_enabled: false,
      idle_days: 10,
      idle_cooldown_days: 30,
      idle_min_days_left: 3,
    });
    expect(await screen.findByText(ru("adm.churn.saved"))).toBeTruthy();
  });
});
