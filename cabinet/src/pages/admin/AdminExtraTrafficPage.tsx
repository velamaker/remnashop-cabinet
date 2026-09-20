import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import {
  extraTrafficAdminApi,
  type ExtraTrafficAdminResponse,
  type ExtraTrafficConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { useI18n } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { pluralFor } from "@/lib/pluralRu";
import { formatAdminMoney } from "@/lib/adminMoney";

/**
 * «Докупка трафика» — настройки продажи +N ГБ к текущему периоду трафика.
 *
 * ГЛАВНОЕ, ЧТО ДОЛЖЕН ПОНЯТЬ ВЛАДЕЛЕЦ НА ЭТОЙ СТРАНИЦЕ: прибавка живёт НЕ до конца
 * подписки, а до ближайшего обновления трафика по правилам тарифа. Иначе один
 * платёж на годовой подписке отдавал бы +N ГБ каждый месяц. Поэтому здесь же
 * показано, какие стратегии обновления реально встречаются у его подписок: при
 * ежедневном (DAY) прибавка живёт меньше суток, и цену за неё брать как за месяц
 * нечестно.
 *
 * ЦЕНУ СТАВИТ ВЛАДЕЛЕЦ, а не формула. Шаг между соседними тарифами включает ещё и
 * устройства, поэтому он — верхняя граница цены докупки, а не сама цена. Докупка
 * должна быть заметно дешевле шага: иначе выгоднее сразу перейти на тариф побольше,
 * и докупка съест апгрейды.
 *
 * Пустая цена = продажи закрыты даже при включённом тумблере: счёт на 0 ₽ шлюз не
 * примет, а с баланса это была бы раздача трафика.
 */

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const INPUT =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";

// Код стратегии панели → КЛЮЧ подписи. Сам текст живёт в словаре: админка русская
// и английская, а коды (DAY, MONTH_ROLLING) приходят из RemnaWave и не переводятся.
const STRATEGY_KEY: Record<string, string> = {
  NO_RESET: "adm.extratraffic.strategy_no_reset",
  DAY: "adm.extratraffic.strategy_day",
  WEEK: "adm.extratraffic.strategy_week",
  MONTH: "adm.extratraffic.strategy_month",
  MONTH_ROLLING: "adm.extratraffic.strategy_month_rolling",
};

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 py-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
      />
      <span className="min-w-0">
        <span className="block text-sm text-fg">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-fg-muted">{hint}</span>}
      </span>
    </label>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  onChange,
  allowEmpty = false,
}: {
  id: string;
  label: string;
  hint?: string;
  value: number | null;
  onChange: (value: number | null) => void;
  allowEmpty?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs text-fg-muted">
        {label}
      </label>
      <input
        id={id}
        type="number"
        min={allowEmpty ? 0 : 1}
        value={value ?? ""}
        onChange={(e) => {
          const raw = e.target.value.trim();
          if (raw === "") {
            onChange(allowEmpty ? null : 0);
            return;
          }
          onChange(Number(raw));
        }}
        className={INPUT}
      />
      {hint && <p className="mt-1 text-xs text-fg-muted">{hint}</p>}
    </div>
  );
}

export function AdminExtraTrafficPage() {
  const { lang, t } = useI18n();
  const [data, setData] = useState<ExtraTrafficAdminResponse | null>(null);
  const [form, setForm] = useState<ExtraTrafficConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    extraTrafficAdminApi
      .get()
      .then((res) => {
        setData(res);
        setForm(res.config);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? e.detail : translate("adm.extratraffic.load_failed")),
      );
    // translate, а не t: иначе смена языка перезагружает данные и стирает
    // несохранённую правку формы.
  }, []);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setNote(null);
    setError(null);
    try {
      const res = await extraTrafficAdminApi.update(form);
      setForm(res.config);
      setData((prev) =>
        prev ? { ...prev, config: res.config, effective_enabled: res.effective_enabled } : prev,
      );
      setNote(
        res.effective_enabled
          ? t("adm.extratraffic.saved_open")
          : t("adm.extratraffic.saved_closed"),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.extratraffic.save_failed"));
    } finally {
      setSaving(false);
    }
  };

  if (error && !form) return <p className="text-sm text-danger">{error}</p>;
  if (!form || !data) return <p className="text-sm text-fg-muted">{t("adm.extratraffic.loading")}</p>;

  const summary = data.summary ?? {};
  const set = (patch: Partial<ExtraTrafficConfig>) => setForm({ ...form, ...patch });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <Gauge className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">{t("adm.extratraffic.title")}</h1>
      </div>

      <section className={SECTION}>
        <Toggle
          label={t("adm.extratraffic.enable")}
          hint={t("adm.extratraffic.enable_hint")}
          checked={form.enabled}
          onChange={(enabled) => set({ enabled })}
        />
        {form.enabled && form.price_rub == null && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            {t("adm.extratraffic.no_price_warn")}
          </p>
        )}
        {form.enabled && data.short_window && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            {t("adm.extratraffic.short_window_warn")}
          </p>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <NumberField
            id="etraffic-gb"
            label={t("adm.extratraffic.gb_label")}
            hint={t("adm.extratraffic.gb_hint")}
            value={form.gb_per_purchase}
            onChange={(v) => set({ gb_per_purchase: v ?? 1 })}
          />
          <NumberField
            id="etraffic-price"
            label={t("adm.extratraffic.price_label")}
            hint={t("adm.extratraffic.price_hint")}
            value={form.price_rub}
            onChange={(price_rub) => set({ price_rub })}
            allowEmpty
          />
          <NumberField
            id="etraffic-min"
            label={t("adm.extratraffic.min_amount_label")}
            hint={t("adm.extratraffic.min_amount_hint")}
            value={form.min_amount_rub}
            onChange={(v) => set({ min_amount_rub: v ?? 1 })}
          />
          <NumberField
            id="etraffic-percent"
            label={t("adm.extratraffic.percent_label")}
            hint={t("adm.extratraffic.percent_hint")}
            value={form.show_from_percent}
            onChange={(v) => set({ show_from_percent: v ?? 0 })}
            allowEmpty
          />
          <NumberField
            id="etraffic-hours"
            label={t("adm.extratraffic.hours_label")}
            hint={t("adm.extratraffic.hours_hint")}
            value={form.min_hours_left}
            onChange={(v) => set({ min_hours_left: v ?? 0 })}
            allowEmpty
          />
          <NumberField
            id="etraffic-cap"
            label={t("adm.extratraffic.cap_label")}
            hint={t("adm.extratraffic.cap_hint")}
            value={form.max_gb_per_window}
            onChange={(v) => set({ max_gb_per_window: v ?? 1 })}
          />
        </div>

        <div className="mt-4 border-t border-border-subtle pt-2">
          <Toggle
            label={t("adm.extratraffic.notify_users")}
            checked={form.notify_users}
            onChange={(notify_users) => set({ notify_users })}
          />
          <Toggle
            label={t("adm.extratraffic.notify_limited")}
            hint={t("adm.extratraffic.notify_limited_hint")}
            checked={form.notify_limited}
            onChange={(notify_limited) => set({ notify_limited })}
          />
          <Toggle
            label={t("adm.extratraffic.notify_admins")}
            checked={form.notify_admins}
            onChange={(notify_admins) => set({ notify_admins })}
          />
          <Toggle
            label={t("adm.extratraffic.refund")}
            hint={t("adm.extratraffic.refund_hint")}
            checked={form.refund_on_revoke}
            onChange={(refund_on_revoke) => set({ refund_on_revoke })}
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} disabled={saving} className={BUTTON}>
            {saving ? "…" : t("adm.extratraffic.save")}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </section>

      {data.hint.length > 0 && (
        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-fg">{t("adm.extratraffic.step_title")}</h3>
          <p className="mt-0.5 text-xs text-fg-muted">{t("adm.extratraffic.step_desc")}</p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.hint.map((h) => (
              <li key={`${h.from_gb}-${h.to_gb}`} className="text-sm text-fg">
                {t("adm.extratraffic.step_range", { from: h.from_gb, to: h.to_gb })} —{" "}
                <span className="tabular font-medium">{formatAdminMoney("RUB", h.diff_30d_rub)}</span>
                {h.device_diff !== 0 && (
                  <span className="text-fg-muted">
                    {" "}
                    {t("adm.extratraffic.step_devices", {
                      n: `${h.device_diff > 0 ? "+" : ""}${h.device_diff}`,
                    })}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.strategies.length > 0 && (
        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-fg">{t("adm.extratraffic.strategies_title")}</h3>
          <p className="mt-0.5 text-xs text-fg-muted">{t("adm.extratraffic.strategies_desc")}</p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.strategies.map((s) => (
              <li key={s.strategy} className="text-sm text-fg">
                {STRATEGY_KEY[s.strategy] ? t(STRATEGY_KEY[s.strategy]!) : s.strategy} —{" "}
                <span className="tabular font-medium">
                  {t(
                    pluralFor(
                      lang,
                      s.subscriptions,
                      "adm.extratraffic.subs_one",
                      "adm.extratraffic.subs_few",
                      "adm.extratraffic.subs_many",
                    ),
                    { n: s.subscriptions },
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">{t("adm.extratraffic.stats_title")}</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-5">
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extratraffic.stat_purchases")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.applied_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extratraffic.stat_gb")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.gb_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extratraffic.stat_amount")}</p>
            <p className="tabular text-lg font-semibold text-fg">
              {formatAdminMoney("RUB", summary.amount_30d ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extratraffic.stat_active")}</p>
            <p className="tabular text-lg font-semibold text-fg">
              {summary.active_grants ?? 0}
              <span className="ml-1 text-xs font-normal text-fg-muted">
                {t("adm.extratraffic.active_gb", { n: summary.active_gb ?? 0 })}
              </span>
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extratraffic.stat_pending")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.credited_open ?? 0}</p>
          </div>
        </div>
        <p className="mt-4 text-xs text-fg-muted">{t("adm.extratraffic.footer")}</p>
      </section>
    </div>
  );
}

export default AdminExtraTrafficPage;
