import { useEffect, useState } from "react";
import { HeartPulse } from "lucide-react";
import {
  churnSignalsAdminApi,
  type ChurnSignalsAdminResponse,
  type ChurnSignalsConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDateTime } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

/**
 * «Сигналы до ухода» — два сообщения, пока человек ещё с нами.
 *
 * «Всё работает?» — один вопрос через сутки после первого подключения. Главное на
 * странице — ДОЛЯ «не работает» среди ответивших: это люди, которые заплатили или
 * взяли пробный и не могут пользоваться, и раньше мы о них не узнавали вовсе.
 *
 * «Давно не подключался» — тому, у кого действующая НЕПРОБНАЯ подписка (не
 * обязательно оплаченная: выданная вручную, подарочная, импортированная — тоже), а
 * простой перевалил за N дней. Подпись это и говорит: «платящим» было бы неправдой —
 * половина таких подписок без единой оплаты. Мера пользы — сколько после сообщения
 * снова подключились.
 *
 * ИТОГ ПОСЛЕДНЕГО ПРОХОДА виден отдельно, и «панель молчала» — громко: в этом случае
 * крон никому не пишет намеренно (молчание панели ≠ «не подключался»), и без этой
 * плашки владелец видел бы «фича включена, а ничего не происходит». Но только пока
 * сигналы включены: выключены оба — проходов нет, и вместо старого итога (с красной
 * плашкой недельной давности) страница так и говорит — «выключено». Ошибки записи в
 * базу показываются, если были: иначе «спросили 0» при сломанной таблице выглядело бы
 * как «некого спрашивать».
 */

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const INPUT =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";

// Причина молчания → ключ перевода. Текст берётся в компоненте: t живёт в контексте.
const REASON_KEY: Record<string, string> = {
  already_asked: "adm.churn.reason_already_asked",
  already_handled: "adm.churn.reason_already_handled",
  staff: "adm.churn.reason_staff",
  no_telegram: "adm.churn.reason_no_telegram",
  blocked: "adm.churn.reason_blocked",
  opted_out: "adm.churn.reason_opted_out",
  frozen: "adm.churn.reason_frozen",
  panel_inactive: "adm.churn.reason_panel_inactive",
  no_panel_data: "adm.churn.reason_no_panel_data",
  not_connected_yet: "adm.churn.reason_not_connected_yet",
  too_early: "adm.churn.reason_too_early",
  too_late: "adm.churn.reason_too_late",
  trial: "adm.churn.reason_trial",
  ends_soon: "adm.churn.reason_ends_soon",
  never_connected: "adm.churn.reason_never_connected",
  recently_online: "adm.churn.reason_recently_online",
  idle_too_long: "adm.churn.reason_idle_too_long",
  cooldown: "adm.churn.reason_cooldown",
  run_cap: "adm.churn.reason_run_cap",
};

type NumKey = "check_delay_hours" | "idle_days" | "idle_cooldown_days" | "idle_min_days_left";

export function AdminChurnSignalsPage() {
  const t = useT();
  const [data, setData] = useState<ChurnSignalsAdminResponse | null>(null);
  const [cfg, setCfg] = useState<ChurnSignalsConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    churnSignalsAdminApi
      .get()
      .then((r) => {
        setData(r);
        setCfg(r.config);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : t("adm.churn.load_error")));
  };

  useEffect(() => {
    load();
    // перевод берём на момент загрузки; перезапрашивать настройки при смене языка не нужно —
    // иначе ответ сервера затрёт незасохранённые правки формы
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!cfg) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const r = await churnSignalsAdminApi.update(cfg);
      setCfg(r.config);
      setNote(t("adm.churn.saved"));
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.churn.save_error"));
    } finally {
      setBusy(false);
    }
  };

  if (!cfg) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4">
        <p className="text-sm text-fg-muted">{error ?? t("adm.churn.loading")}</p>
      </div>
    );
  }

  const stats = data?.stats ?? {};
  // По СОХРАНЁННЫМ настройкам, а не по галочкам формы: сняли галочку, но не сохранили —
  // крон всё ещё ходит, и его итог по-прежнему актуален.
  const saved = data?.config ?? cfg;
  const signalsOff = !saved.check_enabled && !saved.idle_enabled;
  const lastRun = signalsOff ? null : (data?.last_run ?? null);
  const runErrors = lastRun?.errors ?? 0;
  const optouts = data?.optouts ?? { check: 0, idle: 0 };
  const percent = (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : `${value}%`;

  const numberField = (key: NumKey, label: string, min: number, max: number, hint?: string) => (
    <label className="block">
      <span className="text-sm font-medium text-fg">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={cfg[key]}
        onChange={(e) => setCfg({ ...cfg, [key]: Number(e.target.value) })}
        className={`mt-1 ${INPUT}`}
      />
      {hint && <span className="mt-1 block text-xs text-fg-muted">{hint}</span>}
    </label>
  );

  const reasons = (skipped: Record<string, number> | undefined) =>
    Object.entries(skipped ?? {})
      .sort((a, b) => b[1] - a[1])
      .map(([reason, count]) => (
        <div key={reason} className="flex items-center justify-between gap-3 text-sm">
          <span className="text-fg-muted">{REASON_KEY[reason] ? t(REASON_KEY[reason]) : reason}</span>
          <span className="tabular text-fg">{count}</span>
        </div>
      ));

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="flex items-center gap-2 text-lg font-semibold text-fg">
        <HeartPulse className="h-5 w-5 text-accent" /> {t("adm.churn.title")}
      </h1>
      <p className="text-sm text-fg-muted">{t("adm.churn.intro")}</p>

      {lastRun && !lastRun.panel_ok && (
        <div
          role="alert"
          className="rounded-2xl border border-danger/40 bg-danger/10 p-4 text-sm text-fg"
        >
          {t("adm.churn.panel_silent", { at: formatDateTime(lastRun.at) })}
        </div>
      )}

      <div className={SECTION}>
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={cfg.check_enabled}
            onChange={(e) => setCfg({ ...cfg, check_enabled: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
          <span>
            <span className="text-sm font-medium text-fg">{t("adm.churn.check_label")}</span>
            <span className="mt-1 block text-xs text-fg-muted">{t("adm.churn.check_hint")}</span>
          </span>
        </label>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {numberField(
            "check_delay_hours",
            t("adm.churn.check_delay_label"),
            2,
            168,
            t("adm.churn.check_delay_hint", { window: data?.check_window_hours ?? 24 }),
          )}
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <Tile title={t("adm.churn.tile_asked")} value={String(stats.check_sent ?? 0)} />
          <Tile
            title={t("adm.churn.tile_answered")}
            value={`${stats.check_answered ?? 0} · ${percent(stats.check_answered_percent)}`}
          />
          <Tile
            title={t("adm.churn.tile_broken")}
            value={`${stats.check_broken ?? 0} · ${percent(stats.check_broken_percent)}`}
            accent={(stats.check_broken ?? 0) > 0}
          />
        </div>
        <p className="mt-2 text-xs text-fg-muted">{t("adm.churn.broken_share_hint")}</p>
        {(data?.broken ?? []).length > 0 && (
          <div className="mt-3">
            <p className="text-sm font-medium text-fg">{t("adm.churn.broken_list_title")}</p>
            <p className="mt-1 text-xs text-fg-muted">{t("adm.churn.broken_list_hint")}</p>
            <ul className="mt-2 flex flex-col gap-1">
              {(data?.broken ?? []).map((row) => (
                <li key={row.user_id} className="flex items-center justify-between gap-3 text-sm">
                  <span className="tabular text-fg">ID {row.user_id}</span>
                  <span className="text-xs text-fg-muted">
                    {row.answered_at ? formatDateTime(row.answered_at) : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className={SECTION}>
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={cfg.idle_enabled}
            onChange={(e) => setCfg({ ...cfg, idle_enabled: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
          <span>
            <span className="text-sm font-medium text-fg">{t("adm.churn.idle_label")}</span>
            <span className="mt-1 block text-xs text-fg-muted">{t("adm.churn.idle_hint")}</span>
          </span>
        </label>
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {numberField(
            "idle_days",
            t("adm.churn.idle_days_label"),
            3,
            60,
            t("adm.churn.idle_days_hint", { window: data?.idle_window_days ?? 3 }),
          )}
          {numberField("idle_cooldown_days", t("adm.churn.idle_cooldown_label"), 7, 180)}
          {numberField(
            "idle_min_days_left",
            t("adm.churn.idle_min_left_label"),
            1,
            30,
            t("adm.churn.idle_min_left_hint"),
          )}
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Tile title={t("adm.churn.tile_idle_sent")} value={String(stats.idle_sent ?? 0)} />
          <Tile
            title={t("adm.churn.tile_returned")}
            value={`${stats.idle_returned ?? 0} · ${percent(stats.idle_returned_percent)}`}
          />
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-xs text-fg-muted">
          {t("adm.churn.optouts", { check: optouts.check, idle: optouts.idle })}
        </p>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" className={BUTTON} disabled={busy} onClick={save}>
            {t("adm.churn.save")}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-sm font-medium text-fg">{t("adm.churn.last_run_title")}</p>
        {signalsOff ? (
          <p className="mt-1 text-xs text-fg-subtle">{t("adm.churn.last_run_disabled")}</p>
        ) : !lastRun ? (
          <p className="mt-1 text-xs text-fg-subtle">{t("adm.churn.last_run_empty")}</p>
        ) : (
          <>
            <p className="mt-1 text-xs text-fg-muted">
              {t("adm.churn.last_run_line", {
                at: formatDateTime(lastRun.at),
                check: lastRun.sent_check ?? 0,
                idle: lastRun.sent_idle ?? 0,
                failed: lastRun.failed ?? 0,
              })}
            </p>
            {runErrors > 0 && (
              <p className="mt-1 text-xs text-danger">
                {t("adm.churn.last_run_errors", { errors: runErrors })}
              </p>
            )}
            {Object.keys(lastRun.skipped_check ?? {}).length > 0 && (
              <div className="mt-3 flex flex-col gap-1.5">
                <p className="text-xs font-medium text-fg">{t("adm.churn.skipped_check")}</p>
                {reasons(lastRun.skipped_check)}
              </div>
            )}
            {Object.keys(lastRun.skipped_idle ?? {}).length > 0 && (
              <div className="mt-3 flex flex-col gap-1.5">
                <p className="text-xs font-medium text-fg">{t("adm.churn.skipped_idle")}</p>
                {reasons(lastRun.skipped_idle)}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default AdminChurnSignalsPage;

function Tile({ title, value, accent = false }: { title: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg px-4 py-3">
      <p className="text-xs text-fg-muted">{title}</p>
      <p className={`tabular mt-1 text-lg font-semibold ${accent ? "text-danger" : "text-fg"}`}>
        {value}
      </p>
    </div>
  );
}
