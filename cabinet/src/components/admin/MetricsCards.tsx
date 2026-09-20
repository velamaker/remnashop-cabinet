import { useEffect, useState } from "react";
import { statisticsApi, type MetricsRefunds, type MetricsResponse } from "@/api/admin";
import { formatAdminMoney } from "@/lib/adminMoney";
import { gatewayName } from "@/lib/gatewayNames";
import { useT } from "@/i18n/I18nContext";

function fmtMoney(v: number, currency: string): string {
  return `${Math.round(v).toLocaleString("ru-RU")} ${currency === "RUB" ? "₽" : currency}`;
}

/**
 * Что показать в плитке «Возвраты (30 дн)».
 *
 * Главная ловушка — «0 ₽». Бот знает о возврате, только если шлюз о нём сообщает
 * (сейчас Platega и Valutix); ЮMoney и звёзды молчат. Поэтому ноль без оговорки
 * читался бы как «возвратов не было», хотя на деле «не знаем»: рядом с числом
 * всегда перечисляем молчащие шлюзы, а если молчат ВСЕ подключённые — вместо нуля
 * ставим прочерк.
 *
 * Суммы по валютам не складываем (499 ₽ и 5 $ — не «504») и пишем тем же
 * форматом, что блок «Продажи» и графики на этой же странице.
 */
export function refundsTile(
  r: MetricsRefunds,
  blockCurrency: string,
  // Перевод передаём АРГУМЕНТОМ, а не берём модульным translate: функция зовётся
  // во время первого рендера, когда провайдер ещё не успел выставить активный
  // язык, и подсказка приезжала на языке браузера, а не кабинета.
  t: (key: string, vars?: Record<string, string | number>) => string,
): { value: string; hint: string; tone?: "warning" } {
  if (r.count_30d === 0 && r.reporting_gateways.length === 0) {
    return { value: "—", hint: t("adm.stats.refunds_silent_all") };
  }
  const value =
    r.count_30d > 0
      ? r.by_currency.map((c) => formatAdminMoney(c.currency, c.amount)).join(" · ")
      : formatAdminMoney(blockCurrency, 0);
  // Подсказку не склеиваем из кусков: у варианта с молчащими шлюзами свой ключ целиком.
  const hint =
    r.silent_gateways.length > 0
      ? t("adm.stats.refunds_hint_silent", {
          n: r.count_30d,
          gateways: r.silent_gateways.map(gatewayName).join(", "),
        })
      : t("adm.stats.refunds_hint", { n: r.count_30d });
  return r.count_30d > 0 ? { value, hint, tone: "warning" } : { value, hint };
}

/** Одна KPI-плитка. */
function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "accent" | "success" | "warning" | "danger";
}) {
  const toneColor =
    tone === "success"
      ? "text-emerald-500"
      : tone === "warning"
        ? "text-amber-500"
        : tone === "danger"
          ? "text-red-500"
          : "text-fg";
  return (
    <div className="rounded-xl border border-border-subtle bg-bg p-4">
      <div className="text-xs font-medium text-fg-muted">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${toneColor}`}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-fg-subtle">{hint}</div>}
    </div>
  );
}

/** Продуктовые KPI: MRR, ARPU/ARPPU, конверсия trial→оплата, отток, возвраты, топы. */
export function MetricsCards() {
  const t = useT();
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    statisticsApi.metrics().then(setData).catch(() => setError(true));
  }, []);

  if (error) return null;
  if (!data) return null;

  const cur = data.currency;
  // Отток и success rate — чем выше, тем «тревожнее»/«лучше» соответственно.
  const churnTone: "success" | "warning" | "danger" =
    data.churn.pct >= 20 ? "danger" : data.churn.pct >= 10 ? "warning" : "success";
  const successTone: "success" | "warning" | "danger" =
    data.payments.success_pct >= 70 ? "success" : data.payments.success_pct >= 40 ? "warning" : "danger";
  const convTone: "success" | "warning" | "danger" =
    data.conversion.pct >= 15 ? "success" : data.conversion.pct >= 5 ? "warning" : "danger";
  // Старый бэкенд и адаптер «Бедолаги» поля не отдают — тогда плитки просто нет.
  const refunds = data.refunds ? refundsTile(data.refunds, cur, t) : null;

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-fg">{t("adm.stats.kpi_title")}</h3>
        <span className="text-xs text-fg-subtle">
          {t("adm.stats.kpi_currency", { cur: cur === "RUB" ? "₽" : cur })}
        </span>
      </div>
      <p className="mb-3 text-xs text-fg-muted">
        {t("adm.stats.kpi_note", { cur: cur === "RUB" ? t("adm.stats.rubles") : cur })}
        {data.refunds && (
          <>
            {" "}
            {t("adm.stats.kpi_refunds_note")}
          </>
        )}
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Tile
          label={t("adm.stats.mrr")}
          value={fmtMoney(data.mrr, cur)}
          hint={t("adm.stats.mrr_hint", { n: data.mrr_subs })}
          tone="accent"
        />
        <Tile
          label={t("adm.stats.arpu")}
          value={fmtMoney(data.arpu, cur)}
          hint={t("adm.stats.arpu_hint", { n: data.active_users })}
        />
        <Tile
          label={t("adm.stats.arppu")}
          value={fmtMoney(data.arppu, cur)}
          hint={t("adm.stats.arppu_hint", { n: data.payers_30d })}
        />
        <Tile
          label={t("adm.stats.revenue_30d")}
          value={fmtMoney(data.revenue_30d, cur)}
          tone="accent"
        />
        <Tile
          label={t("adm.stats.conversion")}
          value={`${data.conversion.pct}%`}
          hint={t("adm.stats.conversion_hint", { n: data.conversion.converted, total: data.conversion.trials })}
          tone={convTone}
        />
        <Tile
          label={t("adm.stats.churn")}
          value={`${data.churn.pct}%`}
          hint={t("adm.stats.churn_hint", { gone: data.churn.churned_30d, active: data.churn.active_now })}
          tone={churnTone}
        />
        <Tile
          label={t("adm.stats.payments_success")}
          value={`${data.payments.success_pct}%`}
          hint={t("adm.stats.payments_hint", { ok: data.payments.completed_30d, canceled: data.payments.canceled_30d })}
          tone={successTone}
        />
        {refunds && (
          <Tile
            label={t("adm.stats.refunds")}
            value={refunds.value}
            hint={refunds.hint}
            tone={refunds.tone}
          />
        )}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold text-fg-muted">{t("adm.stats.top_plans")}</h4>
          {data.top_plans.length === 0 ? (
            <p className="text-xs text-fg-subtle">{t("adm.stats.no_data")}</p>
          ) : (
            <ul className="space-y-1">
              {data.top_plans.map((p, i) => (
                <li key={i} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-fg">{p.name}</span>
                  <span className="flex-shrink-0 text-fg-muted">
                    {fmtMoney(p.revenue, cur)} · {p.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h4 className="mb-2 text-xs font-semibold text-fg-muted">{t("adm.stats.top_gateways")}</h4>
          {data.top_gateways.length === 0 ? (
            <p className="text-xs text-fg-subtle">{t("adm.stats.no_data")}</p>
          ) : (
            <ul className="space-y-1">
              {data.top_gateways.map((g, i) => (
                <li key={i} className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-fg">{g.gateway_type}</span>
                  <span className="flex-shrink-0 text-fg-muted">
                    {fmtMoney(g.revenue, cur)} · {g.count}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
