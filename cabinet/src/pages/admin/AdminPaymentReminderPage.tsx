import { useEffect, useState } from "react";
import { BellRing } from "lucide-react";
import {
  paymentReminderAdminApi,
  type PaymentReminderAdminResponse,
  type PaymentReminderConfig,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatAdminMoney } from "@/lib/adminMoney";

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

const REASON_RU: Record<string, string> = {
  paid: "уже заплатил",
  newer_attempt: "ушёл в другой шлюз",
  subscription_changed: "подписка изменилась",
  opted_out: "отказался от напоминаний",
  blocked: "заблокирован или заблокировал бота",
  no_telegram: "нет Telegram",
  staff: "свои и админские счета",
  test_payment: "проверочный платёж",
  unknown_kind: "незнакомый вид счёта",
  cooldown: "писали недавно",
  month_cap: "предел за месяц",
  too_early: "ещё рано",
  too_late: "поздно, молчим",
  already_handled: "уже разобран",
  run_cap: "не поместилось в прогон",
  sent: "отправлено",
  failed: "не доставлено",
};

export default function AdminPaymentReminderPage() {
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
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Не удалось загрузить настройки"));
  };

  useEffect(load, []);

  const save = async () => {
    if (!cfg) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const r = await paymentReminderAdminApi.update(cfg);
      setCfg(r.config);
      setNote(r.effective_enabled ? "Сохранено, напоминания включены" : "Сохранено, напоминания выключены");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  if (!cfg) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4">
        <p className="text-sm text-fg-muted">{error ?? "Загружаем…"}</p>
      </div>
    );
  }

  const backlog = data?.backlog ?? {};
  const conversion = data?.conversion ?? {};

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="flex items-center gap-2 text-lg font-semibold text-fg">
        <BellRing className="h-5 w-5 text-accent" /> Напоминание об оплате
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
            <span className="text-sm font-medium text-fg">Напоминать о незавершённой оплате</span>
            <span className="mt-1 block text-xs text-fg-muted">
              Человеку, который создал счёт и не заплатил, уходит одно сообщение в Telegram с
              кнопкой «Открыть оплату» — она ведёт в кабинет, где счёт оформляется заново по
              текущей цене. Старую ссылку не присылаем никогда.
            </span>
          </span>
        </label>
      </div>

      <div className={SECTION}>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="text-sm font-medium text-fg">Через сколько минут писать</span>
            <input
              type="number"
              min={5}
              max={25}
              value={cfg.delay_minutes}
              onChange={(e) => setCfg({ ...cfg, delay_minutes: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
            <span className="mt-1 block text-xs text-fg-muted">
              Почти все оплаты проходят в первые 10 минут, а неоплаченный счёт закрывается на
              30-й — поэтому окно между ними.
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-fg">Не писать по счетам старше, минут</span>
            <input
              type="number"
              min={15}
              max={180}
              value={cfg.max_age_minutes}
              onChange={(e) => setCfg({ ...cfg, max_age_minutes: Number(e.target.value) })}
              className={`mt-1 ${INPUT}`}
            />
            <span className="mt-1 block text-xs text-fg-muted">
              Если бот молчал (обновление, перезапуск), накопившиеся счета не догоняются:
              позднее напоминание читается как спам.
            </span>
          </label>
          <label className="block">
            <span className="text-sm font-medium text-fg">Не чаще одного раза в, часов</span>
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
            <span className="text-sm font-medium text-fg">Не больше сообщений за 30 дней</span>
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
          <span className="text-sm text-fg">Присылать мне сводку по прогонам</span>
        </label>
        <div className="mt-4 flex items-center gap-3">
          <button type="button" className={BUTTON} disabled={busy} onClick={save}>
            Сохранить
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-sm font-medium text-fg">Сколько денег лежит на полу</p>
        <p className="mt-1 text-xs text-fg-muted">
          Счета клиентов за 30 дней, которые создали и не оплатили — и человек после этого так и
          не заплатил ничем.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Tile title="Брошенных счетов" value={String(backlog.invoices_30d ?? 0)} />
          <Tile title="Людей" value={String(backlog.people_30d ?? 0)} />
          <Tile title="Сумма" value={formatAdminMoney("RUB", backlog.amount_30d ?? 0)} />
        </div>
      </div>

      <div className={SECTION}>
        <p className="text-sm font-medium text-fg">Что делала фича за 30 дней</p>
        <p className="mt-1 text-xs text-fg-muted">
          Отправлено {conversion.sent_30d ?? 0}, из них заплатили в течение суток{" "}
          {conversion.paid_after_30d ?? 0}.
        </p>
        <div className="mt-3 flex flex-col gap-1.5">
          {(data?.summary ?? []).length === 0 && (
            <span className="text-xs text-fg-subtle">Пока ничего не происходило.</span>
          )}
          {(data?.summary ?? []).map((row, i) => (
            <div key={i} className="flex items-center justify-between gap-3 text-sm">
              <span className="text-fg-muted">
                {REASON_RU[row.detail] ?? REASON_RU[row.status] ?? `${row.status} ${row.detail}`}
              </span>
              <span className="tabular text-fg">{row.count}</span>
            </div>
          ))}
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
