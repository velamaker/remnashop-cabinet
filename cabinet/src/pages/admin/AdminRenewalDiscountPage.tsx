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
import { pluralFor } from "@/lib/pluralRu";
import { useI18n, useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

// Горизонт предпросмотра и период итогов — короткими списками: админу нужен ответ
// «что будет в ближайший месяц», а не конструктор дат.
const HORIZONS = [7, 30, 60];
const STATS_PERIODS = [30, 90, 365];

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";
const SELECT =
  "rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);

// «7 дней» и «7 days»: форму счётного слова выбирает pluralFor ПО ЯЗЫКУ, сам текст —
// из словаря. Через pluralRu на английском выходило бы «21 day».
function daysLabel(t: Translate, lang: string, n: number): string {
  return t(
    pluralFor(
      lang,
      n,
      "adm.renewaldiscount.days_one",
      "adm.renewaldiscount.days_few",
      "adm.renewaldiscount.days_many",
    ),
    { n },
  );
}

/** Ответ на «Прислать пример себе». Скидку пример не выдаёт — говорим это явно. */
function exampleKey(telegram: RenewalDiscountTelegramOutcome, push: number): string {
  const withPush = push > 0;
  switch (telegram) {
    case "sent":
      return withPush ? "adm.renewaldiscount.example_sent_push" : "adm.renewaldiscount.example_sent";
    case "no_telegram":
      return withPush
        ? "adm.renewaldiscount.example_no_telegram_push"
        : "adm.renewaldiscount.example_no_telegram";
    case "blocked":
      return withPush
        ? "adm.renewaldiscount.example_blocked_push"
        : "adm.renewaldiscount.example_blocked";
    default:
      return withPush ? "adm.renewaldiscount.example_failed_push" : "adm.renewaldiscount.example_failed";
  }
}

// Подписи каналов — ключами, а не текстом: незнакомый канал печатаем как пришёл.
const CHANNEL_KEYS: Record<string, string> = {
  telegram: "adm.renewaldiscount.channel_telegram",
  push: "adm.renewaldiscount.channel_push",
  email: "adm.renewaldiscount.channel_email",
};

/** Холостой прогон и «пример себе»: проверить фичу, не раздав ни одной скидки. */
function PreviewCard() {
  const { t, lang } = useI18n();
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
      else setError(errorText(e, t("adm.renewaldiscount.check_error")));
    } finally {
      setChecking(false);
    }
  };

  const sendExample = async () => {
    setSending(true);
    setExample(null);
    try {
      const res = await renewalDiscountAdminApi.testSend();
      setExample(t(exampleKey(res.telegram, res.push), { push: res.push }));
    } catch (e) {
      setExample(errorText(e, t("adm.renewaldiscount.example_error")));
    } finally {
      setSending(false);
    }
  };

  if (unsupported) return null;

  const labels = data?.reason_labels ?? {};

  return (
    <section className={SECTION}>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-fg">{t("adm.renewaldiscount.preview_title")}</h3>
        <p className="mt-0.5 text-xs text-fg-muted">
          {t("adm.renewaldiscount.preview_hint", { days: daysLabel(t, lang, horizon) })}
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="renewal-horizon" className="mb-1 block text-xs text-fg-muted">
            {t("adm.renewaldiscount.horizon")}
          </label>
          <select
            id="renewal-horizon"
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
            className={SELECT}
          >
            {HORIZONS.map((d) => (
              <option key={d} value={d}>
                {daysLabel(t, lang, d)}
              </option>
            ))}
          </select>
        </div>
        <button onClick={check} disabled={checking} className={BUTTON}>
          {checking ? "…" : t("adm.renewaldiscount.check")}
        </button>
        <button onClick={sendExample} disabled={sending} className={BUTTON}>
          {sending ? "…" : t("adm.renewaldiscount.send_example")}
        </button>
      </div>
      {example && <p className="mt-3 text-xs text-fg">{example}</p>}
      {error && <p className="mt-3 text-xs text-danger">{error}</p>}
      {data && (
        <div className="mt-4 space-y-3 text-xs text-fg-muted">
          <p className="text-sm text-fg">
            {t(
              data.truncated
                ? "adm.renewaldiscount.summary_truncated"
                : "adm.renewaldiscount.summary",
              { examined: data.examined, granted: data.would_grant },
            )}
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
                    <th className="py-1 pr-3 font-medium">{t("adm.renewaldiscount.col_who")}</th>
                    <th className="py-1 pr-3 font-medium">{t("adm.renewaldiscount.col_expire")}</th>
                    <th className="py-1 pr-3 font-medium">{t("adm.renewaldiscount.col_grant")}</th>
                    <th className="py-1 pr-3 font-medium">{t("adm.renewaldiscount.col_channel")}</th>
                    <th className="py-1 font-medium">{t("adm.renewaldiscount.col_outcome")}</th>
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
                          .map(([name]) => (CHANNEL_KEYS[name] ? t(CHANNEL_KEYS[name]) : name))
                          .join(", ") || "—"}
                      </td>
                      <td className={`py-1 ${row.would_grant ? "text-success" : ""}`}>
                        {row.would_grant
                          ? t("adm.renewaldiscount.outcome_grant")
                          : (labels[row.reason ?? ""] ?? row.reason)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div>
            <p className="mb-1 text-fg-subtle">{t("adm.renewaldiscount.message_preview")}</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-bg p-3 text-[11px] leading-relaxed text-fg">
              {data.message.telegram_html.replace(/<[^>]+>/g, "")}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

// Плитки итогов держат КЛЮЧИ подписей: текст берётся при рендере, по текущему языку.
const STATS_TILES = [
  "adm.renewaldiscount.tile_granted",
  "adm.renewaldiscount.tile_used",
  "adm.renewaldiscount.tile_paid",
  "adm.renewaldiscount.tile_given",
  "adm.renewaldiscount.tile_expired",
  "adm.renewaldiscount.tile_tg_failed",
] as const;

/** Итоги и отзыв открытых скидок. */
function StatsCard() {
  const { t, lang } = useI18n();
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
        // .catch живёт вне рендера — перевод берём модульным translate, не хуком.
        else setError(errorText(e, translate("adm.renewaldiscount.stats_error")));
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
      setRevokeMsg(t("adm.renewaldiscount.revoked", { n: res.revoked }));
      load();
    } catch (e) {
      setRevokeMsg(errorText(e, t("adm.renewaldiscount.revoke_error")));
    } finally {
      setRevoking(false);
      setConfirming(false);
    }
  };

  if (unsupported) return null;

  const active = stats?.active ?? 0;
  const tiles: [string, string | number][] = stats
    ? [
        [STATS_TILES[0], stats.granted],
        [STATS_TILES[1], stats.used],
        [STATS_TILES[2], formatAdminMoney("RUB", stats.paid_rub)],
        [STATS_TILES[3], formatAdminMoney("RUB", stats.discount_given_rub)],
        [STATS_TILES[4], stats.expired],
        [STATS_TILES[5], stats.tg_failed],
      ]
    : [];

  return (
    <section className={SECTION}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg">
          {t("adm.renewaldiscount.stats_title", { days: daysLabel(t, lang, days) })}
        </h3>
        <select
          aria-label={t("adm.renewaldiscount.stats_period")}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className={SELECT}
        >
          {STATS_PERIODS.map((d) => (
            <option key={d} value={d}>
              {daysLabel(t, lang, d)}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="text-xs text-danger">{error}</p>}
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {tiles.map(([key, value]) => (
              <div key={key} className="rounded-xl bg-bg px-3 py-2.5">
                <p className="text-lg font-bold text-fg">{value}</p>
                <p className="text-xs text-fg-muted">{t(key)}</p>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-fg-subtle">{t("adm.renewaldiscount.clients_only")}</p>
          <div className="mt-4 space-y-2 rounded-xl border border-border-subtle bg-bg px-4 py-3">
            <p className="text-xs text-fg-muted">{t("adm.renewaldiscount.active_now", { n: active })}</p>
            {confirming && (
              <p className="text-xs text-danger">
                {t(
                  pluralFor(
                    lang,
                    active,
                    "adm.renewaldiscount.revoke_confirm_one",
                    "adm.renewaldiscount.revoke_confirm_few",
                    "adm.renewaldiscount.revoke_confirm_many",
                  ),
                  { n: active },
                )}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                onClick={revoke}
                disabled={revoking || (!confirming && active === 0)}
                className={`${BUTTON} ${confirming ? "border-danger text-danger" : ""}`}
              >
                {revoking
                  ? "…"
                  : confirming
                    ? t("adm.renewaldiscount.revoke_yes")
                    : t("adm.renewaldiscount.revoke")}
              </button>
              {confirming && (
                <button onClick={() => setConfirming(false)} className={BUTTON}>
                  {t("adm.renewaldiscount.cancel")}
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
  const t = useT();
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <BadgePercent className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.renewaldiscount.title")}</h1>
      </div>
      <RenewalDiscountCard />
      <PreviewCard />
      <StatsCard />
    </div>
  );
}
