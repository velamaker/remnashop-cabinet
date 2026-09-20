import { useEffect, useState } from "react";
import { BellRing } from "lucide-react";
import {
  paymentReminderAdminApi,
  type PaymentReminderAdminResponse,
  type PaymentReminderConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatAdminMoney } from "@/lib/adminMoney";
import { useT } from "@/i18n/I18nContext";

/**
 * «Напоминание об оплате» — одно сообщение тому, кто создал счёт и не заплатил.
 *
 * ЧТО ВАЖНО ПОНИМАТЬ ВЛАДЕЛЬЦУ. Напоминание НЕ присылает старую ссылку на оплату и
 * не воскрешает счёт: человек идёт в кабинет и оформляет оплату заново, с текущей
 * ценой. Так сделано намеренно — старая ссылка живёт вечно, повторная оплата по уже
 * проведённому счёту пропадает молча, а в счёте заморожены цена и скидка.
 *
 * СВОДКА ВНИЗУ — про молчание. Она показывает не только отправленные сообщения, но и
 * кого фича НЕ тронула и почему: «уже заплатил», «ушёл в другой шлюз», «отказался»,
 * «слишком рано». Если что-то настроено неверно, это видно именно здесь.
 */

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const INPUT =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";

// Причина молчания → ключ перевода. Сам текст берётся уже в компоненте: t живёт
// в контексте, а карта — модульная константа.
const REASON_KEY: Record<string, string> = {
  paid: "adm.payreminder.reason_paid",
  newer_attempt: "adm.payreminder.reason_newer_attempt",
  subscription_changed: "adm.payreminder.reason_subscription_changed",
  opted_out: "adm.payreminder.reason_opted_out",
  blocked: "adm.payreminder.reason_blocked",
  no_telegram: "adm.payreminder.reason_no_telegram",
  staff: "adm.payreminder.reason_staff",
  test_payment: "adm.payreminder.reason_test_payment",
  unknown_kind: "adm.payreminder.reason_unknown_kind",
  cooldown: "adm.payreminder.reason_cooldown",
  month_cap: "adm.payreminder.reason_month_cap",
  too_early: "adm.payreminder.reason_too_early",
  too_late: "adm.payreminder.reason_too_late",
  already_handled: "adm.payreminder.reason_already_handled",
  run_cap: "adm.payreminder.reason_run_cap",
  sent: "adm.payreminder.reason_sent",
  failed: "adm.payreminder.reason_failed",
};

export default function AdminPaymentReminderPage() {
  const t = useT();
  const [data, setData] = useState<PaymentReminderAdminResponse | null>(null);
  const [cfg, setCfg] = useState<PaymentReminderConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    paymentReminderAdminApi
      .get()
      .then((r) => {
        setData(r);
        setCfg(r.config);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? e.detail : t("adm.payreminder.load_error")),
      );
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
      const r = await paymentReminderAdminApi.update(cfg);
      setCfg(r.config);
      setNote(
        r.effective_enabled
          ? t("adm.payreminder.saved_on")
          : t("adm.payreminder.saved_off"),
      );
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.payreminder.save_error"));
    } finally {
      setBusy(false);
    }
  };

  if (!cfg) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4">
        <p className="text-sm text-fg-muted">{error ?? t("adm.payreminder.loading")}</p>
      </div>
    );
  }

  const backlog = data?.backlog ?? {};
  const conversion = data?.conversion ?? {};

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="flex items-center gap-2 text-lg font-semibold text-fg">
        <BellRing className="h-5 w-5 text-accent" /> {t("adm.payreminder.title")}
      </h1>

      <div className={SECTION}>
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
          <span>
            <span className="text-sm font-medium text-fg">
              {t("adm.payreminder.enable_label")}
            </span>
            <span className="mt-1 block text-xs text-fg-muted">
              {t("adm.payreminder.enable_hint")}
            </span>
          </span>
        </label>
      </div>

      <div className={SECTION}>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm font-medium text-fg">
              {t("adm.payreminder.delay_label")}
            </span>
            <input
              type="number"
              min={5}
              max={25}
              value={cfg.delay_minutes}
              onChange={(e) => setCfg({ ...cfg, delay_minutes: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
            <span className="mt-1 block text-xs text-fg-muted">
              {t("adm.payreminder.delay_hint")}
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-fg">
              {t("adm.payreminder.max_age_label")}
            </span>
            <input
              type="number"
              min={15}
              max={180}
              value={cfg.max_age_minutes}
              onChange={(e) => setCfg({ ...cfg, max_age_minutes: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
            <span className="mt-1 block text-xs text-fg-muted">
              {t("adm.payreminder.max_age_hint")}
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-fg">
              {t("adm.payreminder.cooldown_label")}
            </span>
            <input
              type="number"
              min={1}
              max={168}
              value={cfg.cooldown_hours}
              onChange={(e) => setCfg({ ...cfg, cooldown_hours: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium text-fg">
              {t("adm.payreminder.cap_label")}
            </span>
            <input
              type="number"
              min={1}
              max={30}
              value={cfg.max_per_30d}
              onChange={(e) => setCfg({ ...cfg, max_per_30d: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
          </label>
        </div>
        <label className="mt-4 flex items-start gap-3">
          <input
            type="checkbox"
            checked={cfg.notify_admins}
            onChange={(e) => setCfg({ ...cfg, notify_admins: e.target.checked })}
            className="mt-1 h-4 w-4"
          />
          <span className="text-sm text-fg">{t("adm.payreminder.notify_admins")}</span>
        </label>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" className={BUTTON} disabled={busy} onClick={save}>
            {t("adm.payreminder.save")}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-sm font-medium text-fg">{t("adm.payreminder.backlog_title")}</p>
        <p className="mt-1 text-xs text-fg-muted">{t("adm.payreminder.backlog_hint")}</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Tile
            title={t("adm.payreminder.tile_invoices")}
            value={String(backlog.invoices_30d ?? 0)}
          />
          <Tile
            title={t("adm.payreminder.tile_people")}
            value={String(backlog.people_30d ?? 0)}
          />
          <Tile
            title={t("adm.payreminder.tile_amount")}
            value={formatAdminMoney("RUB", backlog.amount_30d ?? 0)}
          />
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-sm font-medium text-fg">{t("adm.payreminder.conversion_title")}</p>
        <p className="mt-1 text-xs text-fg-muted">
          {t("adm.payreminder.conversion_hint", {
            sent: conversion.sent_30d ?? 0,
            paid: conversion.paid_after_30d ?? 0,
          })}
        </p>
        <div className="mt-3 flex flex-col gap-1.5">
          {(data?.summary ?? []).length === 0 && (
            <span className="text-xs text-fg-subtle">{t("adm.payreminder.summary_empty")}</span>
          )}
          {(data?.summary ?? []).map((row, i) => {
            const key = REASON_KEY[row.detail] ?? REASON_KEY[row.status];
            return (
              <div key={i} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-fg-muted">
                  {key ? t(key) : `${row.status} ${row.detail}`}
                </span>
                <span className="tabular text-fg">{row.count}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Tile({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg px-4 py-3">
      <p className="text-xs text-fg-muted">{title}</p>
      <p className="tabular mt-1 text-lg font-semibold text-fg">{value}</p>
    </div>
  );
}
