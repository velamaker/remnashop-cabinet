import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import {
  extraTrafficAdminApi,
  type ExtraTrafficAdminResponse,
  type ExtraTrafficConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
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

const STRATEGY_RU: Record<string, string> = {
  NO_RESET: "без обновления",
  DAY: "каждый день",
  WEEK: "каждую неделю",
  MONTH: "1-го числа",
  MONTH_ROLLING: "раз в месяц от даты создания",
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
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Не удалось загрузить настройки"));
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
          ? "Сохранено. Докупка трафика открыта."
          : "Сохранено. Докупка закрыта: включите тумблер, задайте цену и объём.",
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
  const set = (patch: Partial<ExtraTrafficConfig>) => setForm({ ...form, ...patch });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <Gauge className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">Докупка трафика</h1>
      </div>

      <section className={SECTION}>
        <Toggle
          label="Продавать докупку трафика"
          hint="Человек добавляет объём к ТЕКУЩЕМУ периоду трафика за фиксированную цену. Прибавка действует до ближайшего обновления трафика по правилам тарифа, а не до конца подписки."
          checked={form.enabled}
          onChange={(enabled) => set({ enabled })}
        />
        {form.enabled && form.price_rub == null && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            Цена не задана — продажи всё равно закрыты. Укажите цену ниже.
          </p>
        )}
        {form.enabled && data.short_window && (
          <p className="mt-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
            У части подписок трафик обновляется каждый день или каждую неделю — там прибавка
            проживёт меньше суток или недели. Цена за неё та же, что за месячную: подумайте,
            стоит ли продавать её на таких тарифах.
          </p>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <NumberField
            id="etraffic-gb"
            label="Сколько ГБ даёт одна покупка"
            hint="Объём фиксированный. Докупок за период сколько угодно — они складываются."
            value={form.gb_per_purchase}
            onChange={(v) => set({ gb_per_purchase: v ?? 1 })}
          />
          <NumberField
            id="etraffic-price"
            label="Цена одной покупки, ₽"
            hint="Пусто — продажи закрыты. От остатка времени цена НЕ зависит: покупается объём, а не срок."
            value={form.price_rub}
            onChange={(price_rub) => set({ price_rub })}
            allowEmpty
          />
          <NumberField
            id="etraffic-min"
            label="Минимальная сумма счёта, ₽"
            hint="Защита от копеечных счетов и минимумов платёжных шлюзов."
            value={form.min_amount_rub}
            onChange={(v) => set({ min_amount_rub: v ?? 1 })}
          />
          <NumberField
            id="etraffic-percent"
            label="Показывать на Главной с расхода, %"
            hint="Ниже этого порога кабинет не предлагает докупку и не делает запрос. При «трафик закончился» карточка показывается всегда."
            value={form.show_from_percent}
            onChange={(v) => set({ show_from_percent: v ?? 0 })}
            allowEmpty
          />
          <NumberField
            id="etraffic-hours"
            label="Не продавать, если до обновления меньше, ч."
            hint="Защита от «заплатил за час»: перед самым обновлением трафик и так придёт бесплатно."
            value={form.min_hours_left}
            onChange={(v) => set({ min_hours_left: v ?? 0 })}
            allowEmpty
          />
          <NumberField
            id="etraffic-cap"
            label="Потолок докупок на один период, ГБ"
            hint="Ограничение техническое, а не коммерческое: защита от опечатки в цене и от зацикленной кнопки."
            value={form.max_gb_per_window}
            onChange={(v) => set({ max_gb_per_window: v ?? 1 })}
          />
        </div>

        <div className="mt-4 border-t border-border-subtle pt-2">
          <Toggle
            label="Сообщать пользователю о покупке и об окончании прибавки"
            checked={form.notify_users}
            onChange={(notify_users) => set({ notify_users })}
          />
          <Toggle
            label="Писать «трафик закончился — можно докупить»"
            hint="Отдельное сообщение рядом с обычным уведомлением бота, с датой обновления трафика и ссылкой в кабинет. Уходит только тем, кто действительно может купить, и один раз на период."
            checked={form.notify_limited}
            onChange={(notify_limited) => set({ notify_limited })}
          />
          <Toggle
            label="Сообщать админам о каждой докупке"
            checked={form.notify_admins}
            onChange={(notify_admins) => set({ notify_admins })}
          />
          <Toggle
            label="При отзыве прибавки возвращать деньги на баланс"
            hint="Выключено по умолчанию. Это значение подставляется в окно отзыва в карточке пользователя — там решение принимается каждый раз отдельно."
            checked={form.refund_on_revoke}
            onChange={(refund_on_revoke) => set({ refund_on_revoke })}
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
          <h3 className="text-sm font-semibold text-fg">Сколько сейчас стоит шаг по трафику</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Разница между соседними тарифами за 30 дней. В неё входят ещё и устройства, а объём
            тарифа остаётся навсегда — поэтому докупку обычно ставят заметно дешевле шага.
          </p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.hint.map((h) => (
              <li key={`${h.from_gb}-${h.to_gb}`} className="text-sm text-fg">
                {h.from_gb} → {h.to_gb} ГБ —{" "}
                <span className="tabular font-medium">{formatAdminMoney("RUB", h.diff_30d_rub)}</span>
                {h.device_diff !== 0 && (
                  <span className="text-fg-muted">
                    {" "}
                    (и {h.device_diff > 0 ? "+" : ""}
                    {h.device_diff} устр.)
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.strategies.length > 0 && (
        <section className={SECTION}>
          <h3 className="text-sm font-semibold text-fg">Когда у людей обновляется трафик</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            До этого момента и живёт прибавка. Считается по правилам панели и по дате создания
            пользователя в ней — то же число видят кабинет, сообщение «трафик закончился» и
            карточка подписки в боте.
          </p>
          <ul className="mt-3 flex flex-col gap-1.5">
            {data.strategies.map((s) => (
              <li key={s.strategy} className="text-sm text-fg">
                {STRATEGY_RU[s.strategy] ?? s.strategy} —{" "}
                <span className="tabular font-medium">{s.subscriptions}</span> подписок
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">За 30 дней</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-5">
          <div>
            <p className="text-xs text-fg-muted">Докупок</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.applied_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Продано, ГБ</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.gb_30d ?? 0}</p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Сумма</p>
            <p className="tabular text-lg font-semibold text-fg">
              {formatAdminMoney("RUB", summary.amount_30d ?? 0)}
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Действует сейчас</p>
            <p className="tabular text-lg font-semibold text-fg">
              {summary.active_grants ?? 0}
              <span className="ml-1 text-xs font-normal text-fg-muted">
                ({summary.active_gb ?? 0} ГБ)
              </span>
            </p>
          </div>
          <div>
            <p className="text-xs text-fg-muted">Ждут применения</p>
            <p className="tabular text-lg font-semibold text-fg">{summary.credited_open ?? 0}</p>
          </div>
        </div>
        <p className="mt-4 text-xs text-fg-muted">
          Прибавка добавляется к лимиту текущего периода и снимается, когда панель обновляет
          трафик. При продлении подписки она гаснет сама: продление и так обнуляет расход и
          выдаёт полный объём тарифа. При смене тарифа сгорает — человека предупреждают об этом
          на экране оплаты. Пауза и резерв закрывают прибавку, не трогая лимит в панели.
          Отозвать докупку вручную — в карточке пользователя.
        </p>
      </section>
    </div>
  );
}

export default AdminExtraTrafficPage;
