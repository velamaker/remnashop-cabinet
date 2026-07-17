import { useEffect, useState } from "react";
import { statisticsApi, type CohortsResponse } from "@/api/admin";

// Цвет ячейки по проценту удержания (0 → прозрачно, 100 → насыщенный акцент).
function cellStyle(pct: number): React.CSSProperties {
  const a = Math.max(0, Math.min(1, pct / 100));
  return { backgroundColor: `color-mix(in srgb, var(--accent) ${Math.round(a * 100)}%, transparent)` };
}

/** Когортное удержание: строки — месяц первой покупки, столбцы — смещение месяцев. */
export function CohortHeatmap() {
  const [data, setData] = useState<CohortsResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    statisticsApi.cohorts(12).then(setData).catch(() => setError(true));
  }, []);

  if (error) return null;
  if (!data) return null;
  if (data.cohorts.length === 0)
    return (
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h3 className="text-sm font-semibold text-fg">Когортное удержание</h3>
        <p className="mt-2 text-sm text-fg-muted">Пока нет данных по платежам.</p>
      </section>
    );

  const cols = Array.from({ length: data.max_offset + 1 }, (_, i) => i);

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-fg">Когортное удержание</h3>
      </div>
      <p className="mb-3 text-xs text-fg-muted">
        Строка — месяц первой покупки. Столбец «+K» — доля когорты, заплативших через K месяцев.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-1 text-xs">
          <thead>
            <tr>
              <th className="px-2 py-1 text-left font-medium text-fg-muted">Когорта</th>
              <th className="px-2 py-1 text-right font-medium text-fg-muted">Размер</th>
              {cols.map((k) => (
                <th key={k} className="px-2 py-1 text-center font-medium text-fg-muted">
                  +{k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.cohorts.map((row) => (
              <tr key={row.cohort}>
                <td className="whitespace-nowrap px-2 py-1 font-medium text-fg">{row.cohort}</td>
                <td className="px-2 py-1 text-right text-fg-muted">{row.size}</td>
                {cols.map((k) => {
                  const cell = row.retention.find((c) => c.offset === k);
                  if (!cell || (k > 0 && cell.users === 0)) {
                    return <td key={k} className="rounded px-2 py-1 text-center text-fg-subtle">·</td>;
                  }
                  return (
                    <td
                      key={k}
                      title={`${cell.users} чел.`}
                      className="rounded px-2 py-1 text-center font-medium text-fg"
                      style={cellStyle(cell.pct)}
                    >
                      {cell.pct}%
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
