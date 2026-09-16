import { useEffect, useState } from "react";
import { statisticsApi, type MetricsRefunds, type MetricsResponse } from "@/api/admin";
import { formatAdminMoney } from "@/lib/adminMoney";
import { gatewayName } from "@/lib/gatewayNames";

// Админка осознанно на русском (см. роадмап, Фаза 12) — не i18n.

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
): { value: string; hint: string; tone?: "warning" } {
  if (r.count_30d === 0 && r.reporting_gateways.length === 0) {
    return { value: "—", hint: "подключённые шлюзы о возвратах не сообщают" };
  }
  const value =
    r.count_30d > 0
      ? r.by_currency.map((c) => formatAdminMoney(c.currency, c.amount)).join(" · ")
      : formatAdminMoney(blockCurrency, 0);
  let hint = `платежей: ${r.count_30d}`;
  if (r.silent_gateways.length > 0) {
    hint += ` · не сообщают: ${r.silent_gateways.map(gatewayName).join(", ")}`;
  }
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
  const refunds = data.refunds ? refundsTile(data.refunds, cur) : null;

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-fg">Ключевые метрики</h3>
        <span className="text-xs text-fg-subtle">валюта: {cur === "RUB" ? "₽" : cur}</span>
      </div>
      <p className="mb-3 text-xs text-fg-muted">
        Денежные метрики — в {cur === "RUB" ? "рублях" : cur} (валюты не суммируются). MRR — оценка по
        последнему платежу активных подписчиков, нормированному к 30 дням.
        {data.refunds && (
          <>
            {" "}
            Возвраты — платежи, которые шлюз отозвал уже после оплаты (чарджбэк), по дню, когда
            бот об этом узнал; в выручку и MRR они не входят. Возврат, сделанный вручную в кабинете
            шлюза или кошелька, и возвраты по выключенным шлюзам бот не видит.
          </>
        )}
      </p>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Tile
          label="MRR (оценка)"
          value={fmtMoney(data.mrr, cur)}
          hint={`по ${data.mrr_subs} подпискам`}
          tone="accent"
        />
        <Tile
          label="ARPU (30 дн)"
          value={fmtMoney(data.arpu, cur)}
          hint={`на ${data.active_users} активных`}
        />
        <Tile
          label="ARPPU (30 дн)"
          value={fmtMoney(data.arppu, cur)}
          hint={`на ${data.payers_30d} плативших`}
        />
        <Tile
          label="Выручка 30 дн"
          value={fmtMoney(data.revenue_30d, cur)}
          tone="accent"
        />
        <Tile
          label="Конверсия trial→оплата"
          value={`${data.conversion.pct}%`}
          hint={`${data.conversion.converted} из ${data.conversion.trials}`}
          tone={convTone}
        />
        <Tile
          label="Отток (30 дн)"
          value={`${data.churn.pct}%`}
          hint={`ушло ${data.churn.churned_30d} · активно ${data.churn.active_now}`}
          tone={churnTone}
        />
        <Tile
          label="Успешность оплат (30 дн)"
          value={`${data.payments.success_pct}%`}
          hint={`${data.payments.completed_30d} ок · ${data.payments.canceled_30d} отмен`}
          tone={successTone}
        />
        {refunds && (
          <Tile label="Возвраты (30 дн)" value={refunds.value} hint={refunds.hint} tone={refunds.tone} />
        )}
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold text-fg-muted">Топ-тарифы по выручке</h4>
          {data.top_plans.length === 0 ? (
            <p className="text-xs text-fg-subtle">Нет данных.</p>
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
          <h4 className="mb-2 text-xs font-semibold text-fg-muted">Топ-шлюзы по выручке</h4>
          {data.top_gateways.length === 0 ? (
            <p className="text-xs text-fg-subtle">Нет данных.</p>
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
