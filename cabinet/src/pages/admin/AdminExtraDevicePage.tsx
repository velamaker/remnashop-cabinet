import { useEffect, useState } from "react";
import { MonitorSmartphone } from "lucide-react";
import {
  extraDeviceAdminApi,
  type ExtraDeviceAdminResponse,
  type ExtraDeviceConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatAdminMoney } from "@/lib/adminMoney";

/**
 * «Докупка устройств» — настройки продажи +1 места к текущей подписке.
 *
 * ЦЕНУ СТАВИТ ВЛАДЕЛЕЦ, а не формула. Разница между соседними тарифами включает ещё
 * и трафик, поэтому она — верхняя граница цены устройства, а не сама цена. Подсказка
 * ниже считается живьём из витрины и служит ориентиром.
 *
 * Пустая цена = продажи закрыты даже при включённом тумблере: счёт на 0 ₽ шлюз не
 * примет, а с баланса это была бы раздача мест.
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
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Не удалось загрузить настройки"));
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
          ? "Сохранено. Докупка открыта."
          : "Сохранено. Докупка закрыта: включите тумблер и задайте цену.",
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  if (error && !form) return <p className="text-sm text-danger">{error}</p>;
  if (!form || !data) return <p className="text-sm text-fg-muted">Загрузка…</p>;

  const summary = data.summary ?? {};
  const set = (patch: Partial<ExtraDeviceConfig>) => setForm({ ...form, ...patch });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <MonitorSmartphone className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">Докупка устройств</h1>
      </div>

      <section className={SECTION}>
        <Toggle
          label="Продавать докупку устройств"
          hint="Человек добавляет +1 место к текущей подписке и платит только за оставшиеся дни."
          checked={form.enabled}
          onChange={(enabled) => set({ enabled })}
        />
        {form.enabled && form.price_rub_30d == null && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            Цена не задана — продажи всё равно закрыты. Укажите цену ниже.
          </p>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <NumberField
            id="extra-price"
            label="Цена за 1 устройство в 30 дней, ₽"
            hint="Пусто — продажи закрыты. Остаток срока считается пропорционально."
            value={form.price_rub_30d}
            onChange={(price_rub_30d) => set({ price_rub_30d })}
            allowEmpty
          />
          <NumberField
            id="extra-min"
            label="Минимальная сумма счёта, ₽"
            hint="Защита от копеечных счетов и минимумов платёжных шлюзов."
            value={form.min_amount_rub}
            onChange={(v) => set({ min_amount_rub: v ?? 1 })}
          />
          <NumberField
            id="extra-days"
            label="Не продавать, если до конца срока меньше, дн."
            hint="На коротком остатке выгоднее продлить саму подписку."
            value={form.min_days_left}
            onChange={(v) => set({ min_days_left: v ?? 1 })}
          />
          <NumberField
            id="extra-max"
            label="Максимум докупленных мест на подписку"
            hint="Дальше человеку предлагается тариф побольше — в нём есть ещё и трафик."
            value={form.max_extra}
            onChange={(v) => set({ max_extra: v ?? 1 })}
          />
        </div>

        <div className="mt-4 border-t border-border-subtle pt-2">
          <Toggle
            label="Отключать устройства, подключённые после покупки места"
            hint="По умолчанию выключено. При включении за 3 дня до конца придёт предупреждение, а в конце срока отключатся устройства, зарегистрированные ПОСЛЕ покупки места, начиная с самого нового, и ровно столько, сколько сверх лимита."
            checked={form.remove_excess_devices}
            onChange={(remove_excess_devices) => set({ remove_excess_devices })}
          />
          <Toggle
            label="Сообщать пользователю"
            checked={form.notify_users}
            onChange={(notify_users) => set({ notify_users })}
          />
          <Toggle
            label="Сообщать админам о каждой докупке"
            checked={form.notify_admins}
            onChange={(notify_admins) => set({ notify_admins })}
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} disabled={saving} className={BUTTON}>
            {saving ? "…" : "Сохранить"}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </section>

      {data.hint.length > 0 && (
        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-fg">Сколько сейчас стоит шаг по устройствам</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Разница между соседними тарифами за 30 дней. В неё входит ещё и трафик, поэтому
            устройство без трафика обычно ставят дешевле шага.
          </p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.hint.map((h) => (
              <li key={`${h.from_devices}-${h.to_devices}`} className="text-sm text-fg">
                {h.from_devices} → {h.to_devices} устр. —{" "}
                <span className="tabular font-medium">{formatAdminMoney("RUB", h.diff_30d_rub)}</span>
                {h.traffic_diff_gb !== 0 && (
                  <span className="text-fg-muted"> (и {h.traffic_diff_gb > 0 ? "+" : ""}{h.traffic_diff_gb} ГБ)</span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">За 30 дней</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-4">
          <div>
            <p className="text-xs text-fg-muted">Докупок</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.applied_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Сумма</p>
            <p className="tabular text-lg font-semibold text-fg">
              {formatAdminMoney("RUB", summary.amount_30d ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Действующих мест</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.active_slots ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Ждут применения</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.credited_open ?? 0}</p>
          </div>
        </div>
        <p className="mt-4 text-xs text-fg-muted">
          Докупленное место живёт до конца срока подписки. При продлении лимит возвращается
          сам, продлить место человек может в кабинете. При смене тарифа место сгорает, а его
          неиспользованная стоимость идёт днями (если включён перенос остатка). Отменить
          докупку — в карточке пользователя.
        </p>
      </section>
    </div>
  );
}

export default AdminExtraDevicePage;
