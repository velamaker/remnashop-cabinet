import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";
import { LineChart } from "lucide-react";
import { statisticsApi, type DailyStatsResponse } from "@/api/admin";

const PERIODS = [30, 60, 90] as const;

function shortDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()}.${d.getMonth() + 1}`;
}

function fmtMoney(currency: string, amount: number): string {
  const n = amount.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  switch (currency) {
    case "RUB": return `${n} ₽`;
    case "USD": return `$${n}`;
    case "EUR": return `€${n}`;
    case "XTR": return `${n} ⭐`;
    default: return `${n} ${currency}`;
  }
}

const TOOLTIP_STYLE = {
  background: "var(--bg-overlay)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  fontSize: 12,
} as const;

/** Графики регистраций и выручки по дням (данные /statistics/daily). */
export function DailyCharts() {
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [data, setData] = useState<DailyStatsResponse | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);

  useEffect(() => {
    statisticsApi
      .daily(days)
      .then((d) => {
        setData(d);
        // Валюта по умолчанию — самая «выручечная» (первая в списке), если ещё не выбрана.
        setCurrency((cur) =>
          cur && d.currencies.includes(cur) ? cur : d.currencies[0] ?? null,
        );
      })
      .catch(() => setData(null));
  }, [days]);

  const regData = useMemo(
    () =>
      (data?.series ?? []).map((p) => ({
        label: shortDate(p.date),
        registrations: p.registrations,
      })),
    [data],
  );

  const revData = useMemo(
    () =>
      (data?.series ?? []).map((p) => ({
        label: shortDate(p.date),
        revenue: currency ? p.revenue[currency] ?? 0 : 0,
      })),
    [data, currency],
  );

  if (!data) return null;

  const totalRegs = regData.reduce((s, d) => s + d.registrations, 0);
  const totalRev = revData.reduce((s, d) => s + d.revenue, 0);

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <LineChart className="h-5 w-5 text-accent" />
        <h2 className="text-base font-semibold text-fg">Динамика по дням</h2>
        <div className="ml-auto flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setDays(p)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                p === days
                  ? "bg-accent text-white"
                  : "bg-bg-subtle text-fg-muted hover:text-fg"
              }`}
            >
              {p} дней
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Регистрации */}
        <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
          <div className="mb-3 flex items-baseline justify-between">
            <span className="text-sm font-semibold text-fg">Регистрации</span>
            <span className="tabular text-xs text-fg-subtle">{totalRegs} всего</span>
          </div>
          <div className="h-44 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={regData} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: "var(--fg-subtle)" }}
                  interval="preserveStartEnd"
                  minTickGap={24}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "var(--bg-raised)" }}
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "var(--fg)" }}
                  formatter={(v: number) => [v, "Регистраций"]}
                />
                <Bar dataKey="registrations" fill="var(--accent)" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Выручка */}
        <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
          <div className="mb-3 flex items-baseline justify-between gap-2">
            <span className="text-sm font-semibold text-fg">Выручка</span>
            <div className="flex items-center gap-2">
              {currency && (
                <span className="tabular text-xs text-fg-subtle">
                  {fmtMoney(currency, totalRev)}
                </span>
              )}
              {data.currencies.length > 1 && (
                <select
                  value={currency ?? ""}
                  onChange={(e) => setCurrency(e.target.value)}
                  className="rounded-lg border border-border-subtle bg-bg px-2 py-0.5 text-xs text-fg"
                >
                  {data.currencies.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              )}
            </div>
          </div>
          {currency ? (
            <div className="h-44 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={revData} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 10, fill: "var(--fg-subtle)" }}
                    interval="preserveStartEnd"
                    minTickGap={24}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    cursor={{ fill: "var(--bg-raised)" }}
                    contentStyle={TOOLTIP_STYLE}
                    labelStyle={{ color: "var(--fg)" }}
                    formatter={(v: number) => [fmtMoney(currency, v), "Выручка"]}
                  />
                  <Bar dataKey="revenue" fill="var(--accent-2)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="flex h-44 items-center justify-center text-sm text-fg-subtle">
              нет продаж за период
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
