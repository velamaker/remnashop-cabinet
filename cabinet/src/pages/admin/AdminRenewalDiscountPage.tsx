import { useCallback, useEffect, useState } from "react";
import { BadgePercent } from "lucide-react";
import { RenewalDiscountCard } from "./AdminSettingsPage";
import {
  renewalDiscountAdminApi,
  type RenewalDiscountPreview,
  type RenewalDiscountStats,
  type RenewalDiscountTelegramOutcome,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { formatAdminMoney } from "@/lib/adminMoney";
import { pluralRu, ruDays } from "@/lib/pluralRu";

// Горизонт предпросмотра и период итогов — короткими списками: админу нужен ответ
// «что будет в ближайший месяц», а не конструктор дат.
const HORIZONS = [7, 30, 60];
const STATS_PERIODS = [30, 90, 365];

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";
const SELECT =
  "rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);

/** Ответ на «Прислать пример себе». Скидку пример не выдаёт — говорим это явно. */
function exampleText(telegram: RenewalDiscountTelegramOutcome, push: number): string {
  const pushNote = push > 0 ? ` Push: ${push}.` : "";
  switch (telegram) {
    case "sent":
      return `Пример отправлен вам в Telegram. Скидка не выдана.${pushNote}`;
    case "no_telegram":
      return `У вашего аккаунта нет Telegram — отправить пример некуда.${pushNote}`;
    case "blocked":
      return `Telegram не доставил пример: бот у вас заблокирован или чата с ним нет. Скидка не выдана.${pushNote}`;
    default:
      return `Отправить пример в Telegram не удалось. Скидка не выдана.${pushNote}`;
  }
}

const CHANNEL_NAMES: Record<string, string> = { telegram: "Telegram", push: "push", email: "письмо" };

/** Холостой прогон и «пример себе»: проверить фичу, не раздав ни одной скидки. */
function PreviewCard() {
  const [horizon, setHorizon] = useState(30);
  const [data, setData] = useState<RenewalDiscountPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [sending, setSending] = useState(false);
  const [example, setExample] = useState<string | null>(null);

  const check = async () => {
    setChecking(true);
    setError(null);
    try {
      setData(await renewalDiscountAdminApi.preview(horizon));
    } catch (e) {
      setData(null);
      if (e instanceof ApiError && e.status === 501) setUnsupported(true);
      else setError(errorText(e, "Не удалось проверить"));
    } finally {
      setChecking(false);
    }
  };

  const sendExample = async () => {
    setSending(true);
    setExample(null);
    try {
      const res = await renewalDiscountAdminApi.testSend();
      setExample(exampleText(res.telegram, res.push));
    } catch (e) {
      setExample(errorText(e, "Не удалось отправить пример"));
    } finally {
      setSending(false);
    }
  };

  if (unsupported) return null;

  const labels = data?.reason_labels ?? {};

  return (
    <section className={SECTION}>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-fg">Проверка без отправки</h3>
        <p className="mt-0.5 text-xs text-fg-muted">
          Кому выдалась бы скидка в ближайшие {ruDays(horizon)}, если включить сейчас. Ничего не выдаётся и не отправляется.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="renewal-horizon" className="mb-1 block text-xs text-fg-muted">
            Горизонт
          </label>
          <select
            id="renewal-horizon"
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
            className={SELECT}
          >
            {HORIZONS.map((d) => (
              <option key={d} value={d}>
                {ruDays(d)}
              </option>
            ))}
          </select>
        </div>
        <button onClick={check} disabled={checking} className={BUTTON}>
          {checking ? "…" : "Проверить"}
        </button>
        <button onClick={sendExample} disabled={sending} className={BUTTON}>
          {sending ? "…" : "Прислать пример себе"}
        </button>
      </div>
      {example && <p className="mt-3 text-xs text-fg">{example}</p>}
      {error && <p className="mt-3 text-xs text-danger">{error}</p>}
      {data && (
        <div className="mt-4 space-y-3 text-xs text-fg-muted">
          <p className="text-sm text-fg">
            Осмотрено {data.examined}, получат скидку {data.would_grant}
            {data.truncated ? " (показана первая часть — кандидатов больше)" : ""}
          </p>
          {Object.keys(data.skipped).length > 0 && (
            <ul className="space-y-0.5">
              {Object.entries(data.skipped)
                .sort((a, b) => b[1] - a[1])
                .map(([code, count]) => (
                  <li key={code}>
                    {labels[code] ?? code} — {count}
                  </li>
                ))}
            </ul>
          )}
          {data.sample.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-fg-subtle">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Кто</th>
                    <th className="py-1 pr-3 font-medium">Конец подписки</th>
                    <th className="py-1 pr-3 font-medium">Выдача</th>
                    <th className="py-1 pr-3 font-medium">Куда</th>
                    <th className="py-1 font-medium">Итог</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sample.map((row, i) => (
                    <tr key={`${row.user_id ?? "x"}-${i}`} className="border-t border-border-subtle">
                      <td className="py-1 pr-3">{row.user_id ?? "—"}</td>
                      <td className="py-1 pr-3">{formatDate(row.expire_at)}</td>
                      <td className="py-1 pr-3">{formatDate(row.grant_at)}</td>
                      <td className="py-1 pr-3">
                        {Object.entries(row.channels)
                          .filter(([, on]) => on)
                          .map(([name]) => CHANNEL_NAMES[name] ?? name)
                          .join(", ") || "—"}
                      </td>
                      <td className={`py-1 ${row.would_grant ? "text-success" : ""}`}>
                        {row.would_grant ? "получит" : (labels[row.reason ?? ""] ?? row.reason)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div>
            <p className="mb-1 text-fg-subtle">Так выглядит сообщение в Telegram:</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-bg p-3 text-[11px] leading-relaxed text-fg">
              {data.message.telegram_html.replace(/<[^>]+>/g, "")}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

/** Итоги и отзыв открытых скидок. */
function StatsCard() {
  const [days, setDays] = useState(90);
  const [stats, setStats] = useState<RenewalDiscountStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revokeMsg, setRevokeMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    renewalDiscountAdminApi
      .stats(days)
      .then(setStats)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 501) setUnsupported(true);
        else setError(errorText(e, "Не удалось загрузить итоги"));
      });
  }, [days]);

  useEffect(() => {
    load();
  }, [load]);

  // Отзыв необратим: первое нажатие только спрашивает, второе — отзывает.
  const revoke = async () => {
    if (!confirming) {
      setConfirming(true);
      setRevokeMsg(null);
      return;
    }
    setRevoking(true);
    try {
      const res = await renewalDiscountAdminApi.revokeActive();
      setRevokeMsg(`Отозвано: ${res.revoked}`);
      load();
    } catch (e) {
      setRevokeMsg(errorText(e, "Не удалось отозвать"));
    } finally {
      setRevoking(false);
      setConfirming(false);
    }
  };

  if (unsupported) return null;

  const active = stats?.active ?? 0;
  const tiles: [string, string | number][] = stats
    ? [
        ["Выдано", stats.granted],
        ["Воспользовались", stats.used],
        ["Оплачено со скидкой, ₽", formatAdminMoney("RUB", stats.paid_rub)],
        ["Отдано скидкой, ₽", formatAdminMoney("RUB", stats.discount_given_rub)],
        ["Сгорело без покупки", stats.expired],
        ["Не доставлено в Telegram", stats.tg_failed],
      ]
    : [];

  return (
    <section className={SECTION}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">Итоги за {ruDays(days)}</h3>
        <select aria-label="Период итогов" value={days} onChange={(e) => setDays(Number(e.target.value))} className={SELECT}>
          {STATS_PERIODS.map((d) => (
            <option key={d} value={d}>
              {ruDays(d)}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {tiles.map(([label, value]) => (
              <div key={label} className="rounded-xl bg-bg px-3 py-2.5">
                <p className="text-lg font-bold text-fg">{value}</p>
                <p className="text-xs text-fg-muted">{label}</p>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-fg-subtle">
            Считаются только оплаты клиентов: покупки персонала и тестовые платежи исключены.
          </p>
          <div className="mt-4 space-y-2 rounded-xl border border-border-subtle bg-bg px-4 py-3">
            <p className="text-xs text-fg-muted">Сейчас действует: {active}</p>
            {confirming && (
              <p className="text-xs text-danger">
                Отозвать {active} {pluralRu(active, "активную скидку", "активные скидки", "активных скидок")}? Людям
                ничего не придёт, но при оплате скидки уже не будет.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                onClick={revoke}
                disabled={revoking || (!confirming && active === 0)}
                className={`${BUTTON} ${confirming ? "border-danger text-danger" : ""}`}
              >
                {revoking ? "…" : confirming ? "Да, отозвать" : "Отозвать активные скидки"}
              </button>
              {confirming && (
                <button onClick={() => setConfirming(false)} className={BUTTON}>
                  Отмена
                </button>
              )}
            </div>
            {revokeMsg && <p className="text-xs text-fg">{revokeMsg}</p>}
          </div>
        </>
      )}
    </section>
  );
}

// «Скидка до окончания подписки» — раздел Маркетинг. Настройки + проверка на
// сухую + итоги; на бэкенде без этой механики (501) блоки просто не рисуются.
export default function AdminRenewalDiscountPage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <BadgePercent className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Скидка до окончания подписки</h1>
      </div>
      <RenewalDiscountCard />
      <PreviewCard />
      <StatsCard />
    </div>
  );
}
