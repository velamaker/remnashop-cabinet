import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";
import { BarChart3 } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { useT } from "@/i18n/I18nContext";
import { formatBytes } from "@/lib/format";

type Day = { date: string; total: number };

function shortDate(iso: string): string {
  const d = new Date(iso);
  return `${d.getDate()}.${d.getMonth() + 1}`;
}

/** График расхода трафика по дням за 30 дней (данные /subscription/traffic-history). */
export function TrafficChart() {
  const t = useT();
  const [days, setDays] = useState<Day[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    subscriptionApi
      .trafficHistory()
      .then((d) => setDays(d.days))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  // Пока не загрузилось или нет ни одного дня с трафиком — не показываем блок.
  if (!loaded || days.length === 0 || days.every((d) => d.total === 0)) {
    return null;
  }

  const data = days.map((d) => ({ ...d, label: shortDate(d.date) }));
  const totalSum = days.reduce((s, d) => s + d.total, 0);

  return (
    <div className="surface p-5">
      <div className="mb-3 flex items-center justify-between">
        <p className="flex items-center gap-2 text-sm font-medium text-fg-muted">
          <BarChart3 className="h-4 w-4" />
          {t("home.trafficChart30")}
        </p>
        <span className="tabular text-xs text-fg-subtle">{formatBytes(totalSum)}</span>
      </div>
      <div className="h-40 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
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
              contentStyle={{
                background: "var(--bg-overlay)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                fontSize: 12,
              }}
              labelStyle={{ color: "var(--fg)" }}
              formatter={(v: number) => [formatBytes(v), t("home.traffic")]}
            />
            <Bar dataKey="total" fill="var(--accent)" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
