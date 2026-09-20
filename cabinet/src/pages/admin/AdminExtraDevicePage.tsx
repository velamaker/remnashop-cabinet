import { useEffect, useState } from "react";
import { MonitorSmartphone } from "lucide-react";
import {
  extraDeviceAdminApi,
  type ExtraDeviceAdminResponse,
  type ExtraDeviceConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatAdminMoney } from "@/lib/adminMoney";
import { useT } from "@/i18n/I18nContext";

/**
 * «Докупка устройств» — настройки продажи +1 места к текущей подписке.
 *
 * ЦЕНУ СТАВИТ ВЛАДЕЛЕЦ, а не формула. Разница между соседними тарифами включает ещё
 * и трафик, поэтому она — верхняя граница цены устройства, а не сама цена. Подсказка
 * ниже считается живьём из витрины и служит ориентиром.
 *
 * Пустая цена = продажи закрыты даже при включённом тумблере: счёт на 0 ₽ шлюз не
 * примет, а с баланса это была бы раздача мест.
 *
 * Отключение устройств по окончании места ВКЛЮЧЕНО по умолчанию (решение владельца
 * «отключить и предложить снова»): без него панель пускает уже подключённый аппарат
 * без сверки с лимитом, и место, оплаченное на неделю, работало бы вечно.
 */

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const INPUT =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";

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

export function AdminExtraDevicePage() {
  const t = useT();
  const [data, setData] = useState<ExtraDeviceAdminResponse | null>(null);
  const [form, setForm] = useState<ExtraDeviceConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    extraDeviceAdminApi
      .get()
      .then((res) => {
        setData(res);
        setForm(res.config);
      })
      .catch((e) =>
        setError(e instanceof ApiError ? e.detail : t("adm.extradevice.load_error")),
      );
    // перевод берём на момент загрузки; перезапрашивать настройки при смене языка не нужно
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setNote(null);
    setError(null);
    try {
      const res = await extraDeviceAdminApi.update(form);
      setForm(res.config);
      setData((prev) => (prev ? { ...prev, config: res.config, effective_enabled: res.effective_enabled } : prev));
      setNote(
        res.effective_enabled
          ? t("adm.extradevice.saved_open")
          : t("adm.extradevice.saved_closed"),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.extradevice.save_error"));
    } finally {
      setSaving(false);
    }
  };

  if (error && !form) return <p className="text-sm text-danger">{error}</p>;
  if (!form || !data) return <p className="text-sm text-fg-muted">{t("adm.extradevice.loading")}</p>;

  const summary = data.summary ?? {};
  const set = (patch: Partial<ExtraDeviceConfig>) => setForm({ ...form, ...patch });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <MonitorSmartphone className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">{t("adm.extradevice.title")}</h1>
      </div>

      <section className={SECTION}>
        <Toggle
          label={t("adm.extradevice.sell_label")}
          hint={t("adm.extradevice.sell_hint")}
          checked={form.enabled}
          onChange={(enabled) => set({ enabled })}
        />
        {form.enabled && form.price_rub_30d == null && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            {t("adm.extradevice.no_price_warn")}
          </p>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <NumberField
            id="extra-price"
            label={t("adm.extradevice.price_label")}
            hint={t("adm.extradevice.price_hint")}
            value={form.price_rub_30d}
            onChange={(price_rub_30d) => set({ price_rub_30d })}
            allowEmpty
          />
          <NumberField
            id="extra-min"
            label={t("adm.extradevice.min_amount_label")}
            hint={t("adm.extradevice.min_amount_hint")}
            value={form.min_amount_rub}
            onChange={(v) => set({ min_amount_rub: v ?? 1 })}
          />
          <NumberField
            id="extra-days"
            label={t("adm.extradevice.min_days_label")}
            hint={t("adm.extradevice.min_days_hint")}
            value={form.min_days_left}
            onChange={(v) => set({ min_days_left: v ?? 1 })}
          />
          <NumberField
            id="extra-max"
            label={t("adm.extradevice.max_extra_label")}
            hint={t("adm.extradevice.max_extra_hint")}
            value={form.max_extra}
            onChange={(v) => set({ max_extra: v ?? 1 })}
          />
        </div>

        <div className="mt-4 border-t border-border-subtle pt-2">
          <Toggle
            label={t("adm.extradevice.remove_label")}
            hint={t("adm.extradevice.remove_hint")}
            checked={form.remove_excess_devices}
            onChange={(remove_excess_devices) => set({ remove_excess_devices })}
          />
          <Toggle
            label={t("adm.extradevice.notify_users")}
            checked={form.notify_users}
            onChange={(notify_users) => set({ notify_users })}
          />
          <Toggle
            label={t("adm.extradevice.notify_admins")}
            checked={form.notify_admins}
            onChange={(notify_admins) => set({ notify_admins })}
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} disabled={saving} className={BUTTON}>
            {saving ? "…" : t("adm.extradevice.save")}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </section>

      {data.hint.length > 0 && (
        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-fg">{t("adm.extradevice.hint_title")}</h3>
          <p className="mt-0.5 text-xs text-fg-muted">{t("adm.extradevice.hint_note")}</p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.hint.map((h) => (
              <li key={`${h.from_devices}-${h.to_devices}`} className="text-sm text-fg">
                {t("adm.extradevice.hint_row", { from: h.from_devices, to: h.to_devices })}{" "}
                <span className="tabular font-medium">{formatAdminMoney("RUB", h.diff_30d_rub)}</span>
                {h.traffic_diff_gb !== 0 && (
                  <span className="text-fg-muted">
                    {" "}
                    {t("adm.extradevice.hint_traffic", {
                      gb: h.traffic_diff_gb > 0 ? `+${h.traffic_diff_gb}` : h.traffic_diff_gb,
                    })}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">{t("adm.extradevice.stats_title")}</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-4">
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extradevice.stat_purchases")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.applied_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extradevice.stat_amount")}</p>
            <p className="tabular text-lg font-semibold text-fg">
              {formatAdminMoney("RUB", summary.amount_30d ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extradevice.stat_active_slots")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.active_slots ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">{t("adm.extradevice.stat_pending")}</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.credited_open ?? 0}</p>
          </div>
        </div>
        <p className="mt-4 text-xs text-fg-muted">{t("adm.extradevice.footer")}</p>
      </section>
    </div>
  );
}

export default AdminExtraDevicePage;
