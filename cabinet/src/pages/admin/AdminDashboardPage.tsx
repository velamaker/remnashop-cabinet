import { useEffect, useState } from "react";
import { Users, CreditCard, TrendingUp, Activity, AlertCircle, ShoppingCart } from "lucide-react";
import {
  statisticsApi,
  type AdminOverviewResponse,
  type GatewayStats,
  type SalesStatsResponse,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { DailyCharts } from "@/components/admin/DailyCharts";
import { CohortHeatmap } from "@/components/admin/CohortHeatmap";
import { MetricsCards } from "@/components/admin/MetricsCards";
import { formatAdminMoney as fmtMoney } from "@/lib/adminMoney";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      <p className="mt-1 text-2xl font-bold text-fg">{value}</p>
      {sub && <p className="mt-1 text-xs text-fg-subtle">{sub}</p>}
    </div>
  );
}

function GatewayCard({ g }: { g: GatewayStats }) {
  const t = useT();
  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-semibold text-fg">{g.gateway_type}</span>
        <span className="rounded-full bg-accent-subtle px-2 py-0.5 text-xs text-accent">
          {t("adm.stats.gw_payments", { n: g.paid_count })}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <p className="text-fg-muted">{t("adm.stats.total")}</p>
          <p className="font-semibold text-fg">
            {g.total_income.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽
          </p>
        </div>
        <div>
          <p className="text-fg-muted">{t("adm.stats.month")}</p>
          <p className="font-semibold text-fg">
            {g.monthly_income.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽
          </p>
        </div>
        <div>
          <p className="text-fg-muted">{t("adm.stats.week")}</p>
          <p className="font-semibold text-fg">
            {g.weekly_income.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽
          </p>
        </div>
        <div>
          <p className="text-fg-muted">{t("adm.stats.today")}</p>
          <p className="font-semibold text-fg">
            {g.daily_income.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽
          </p>
        </div>
      </div>
    </div>
  );
}

export default function AdminDashboardPage() {
  const t = useT();
  const [data, setData] = useState<AdminOverviewResponse | null>(null);
  const [sales, setSales] = useState<SalesStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    statisticsApi
      .overview()
      .then(setData)
      .catch((e) => {
        // Вне рендера: берём перевод модульной функцией, а не хуком.
        setError(e instanceof ApiError ? e.detail : translate("adm.stats.load_error"));
      })
      .finally(() => setLoading(false));
    // Продажи грузим отдельно — не блокируют обзор, если что-то пойдёт не так.
    statisticsApi.sales().then(setSales).catch(() => {});
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-20 text-center">
        <AlertCircle className="h-10 w-10 text-danger" />
        <p className="text-fg-muted">{error}</p>
      </div>
    );
  }

  if (!data) return null;

  const { users, subscriptions, transactions } = data;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-fg">{t("adm.stats.title")}</h1>

      {/* Users */}
      <section>
        <div className="mb-4 flex items-center gap-2">
          <Users className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">{t("adm.stats.users")}</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <StatCard label={t("adm.stats.total")} value={users.total} />
          <StatCard label={t("adm.stats.active")} value={users.active} />
          <StatCard label={t("adm.stats.blocked")} value={users.blocked} />
          <StatCard label={t("adm.stats.paying")} value={users.paying} />
          <StatCard label={t("adm.stats.new_today")} value={users.new_today} />
          <StatCard label={t("adm.stats.week")} value={users.new_week} />
          <StatCard label={t("adm.stats.month")} value={users.new_month} />
          <StatCard label={t("adm.stats.users_trial")} value={users.with_trial} />
        </div>
      </section>

      {/* Subscriptions */}
      <section>
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">{t("adm.stats.subs")}</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <StatCard label={t("adm.stats.total")} value={subscriptions.total} />
          <StatCard label={t("adm.stats.active")} value={subscriptions.active} />
          <StatCard label={t("adm.stats.expired")} value={subscriptions.expired} />
          <StatCard label={t("adm.stats.disabled")} value={subscriptions.disabled} />
          <StatCard label={t("adm.stats.subs_trial")} value={subscriptions.trial} />
          <StatCard label={t("adm.stats.unlimited")} value={subscriptions.unlimited} />
          <StatCard label={t("adm.stats.limited")} value={subscriptions.limited} />
          <StatCard
            label={t("adm.stats.expiring_soon")}
            value={subscriptions.expiring_soon}
          />
        </div>
      </section>

      {/* Sales 30/60/90 */}
      {sales && sales.periods.length > 0 && (
        <section>
          <div className="mb-4 flex items-center gap-2">
            <ShoppingCart className="h-5 w-5 text-accent" />
            <h2 className="text-base font-semibold text-fg">{t("adm.stats.sales")}</h2>
            <span className="text-xs text-fg-subtle">{t("adm.stats.sales_hint")}</span>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {sales.periods.map((p) => (
              <div key={p.days} className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
                <div className="mb-3 flex items-baseline justify-between">
                  <span className="text-sm font-semibold text-fg">
                    {t("adm.stats.sales_period", { n: p.days })}
                  </span>
                  <span className="rounded-full bg-accent-subtle px-2 py-0.5 text-xs text-accent">
                    {t("adm.stats.sales_count", { n: p.sales_count })}
                  </span>
                </div>
                {p.revenue.length > 0 ? (
                  <div className="space-y-1.5">
                    {p.revenue.map((r) => (
                      <div key={r.currency} className="flex items-baseline justify-between">
                        <span className="text-xs text-fg-muted">{r.currency}</span>
                        <span className="text-lg font-bold text-fg">{fmtMoney(r.currency, r.amount)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-fg-subtle">{t("adm.stats.no_sales")}</p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Ключевые метрики (KPI) */}
      <MetricsCards />

      {/* Динамика по дням (графики) */}
      <DailyCharts />
      <CohortHeatmap />

      {/* Gateways */}
      {transactions.gateways.length > 0 && (
        <section>
          <div className="mb-4 flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-accent" />
            <h2 className="text-base font-semibold text-fg">{t("adm.stats.gateways")}</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {transactions.gateways.map((g) => (
              <GatewayCard key={g.gateway_type} g={g} />
            ))}
          </div>
        </section>
      )}

      {/* Transactions summary */}
      <section>
        <div className="mb-4 flex items-center gap-2">
          <CreditCard className="h-5 w-5 text-accent" />
          <h2 className="text-base font-semibold text-fg">{t("adm.stats.tx")}</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard label={t("adm.stats.total")} value={transactions.total} />
          <StatCard label={t("adm.stats.tx_completed")} value={transactions.completed} />
        </div>
      </section>
    </div>
  );
}
