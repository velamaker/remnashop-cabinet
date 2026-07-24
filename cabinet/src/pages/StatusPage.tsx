import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, CheckCircle2, AlertTriangle, ArrowLeft } from "lucide-react";
import { statusApi, type StatusResponse, type UptimeDay } from "@/api/status";
import { useNodePings } from "@/lib/ping";
import { useBranding } from "@/contexts/BrandingContext";
import { Flag } from "@/components/Flag";
import { useT } from "@/i18n/I18nContext";

// Цвет пинг-бейджа: зелёный <100 мс, жёлтый <250 мс, красный выше.
export function pingClass(ms: number): string {
  if (ms < 100) return "bg-success/10 text-success";
  if (ms < 250) return "bg-warning/10 text-warning";
  return "bg-danger/10 text-danger";
}

// Цвет «свечки» аптайма за день: зелёный ≥99%, жёлтый ≥95%, красный ниже.
function uptimeBarClass(pct: number): string {
  if (pct >= 99) return "bg-success";
  if (pct >= 95) return "bg-warning";
  return "bg-danger";
}

// Полоска аптайма («свечки»). Период и процент — по РЕАЛЬНО имеющимся дням истории,
// а не мнимые «30» (иначе 3 сегмента, а подпись «за месяц» — вводит в заблуждение).
function UptimeBars({ history }: { history: UptimeDay[] }) {
  const t = useT();
  if (!history.length) return null;
  const days = history.length;
  const avg = Math.round(history.reduce((s, d) => s + d.uptime, 0) / days);
  return (
    <div className="mt-2">
      <div className="flex items-center gap-[2px]">
        {history.map((d) => (
          <div
            key={d.date}
            title={`${d.date}: ${d.uptime}%`}
            className={`h-4 flex-1 rounded-[1px] ${uptimeBarClass(d.uptime)}`}
          />
        ))}
      </div>
      <p className="mt-1 text-[11px] text-fg-subtle">
        {t("status.uptimeLabel", { n: days })} <span className="font-semibold text-fg-muted">{avg}%</span>
      </p>
    </div>
  );
}

// Публичная страница статуса — доступна без входа. Автообновление каждые 30 с.
export default function StatusPage() {
  const t = useT();
  const { brandName } = useBranding();
  const [data, setData] = useState<StatusResponse | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => statusApi.get().then((d) => { if (alive) { setData(d); setLoaded(true); } }).catch(() => { if (alive) setLoaded(true); });
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const allOk = data?.all_operational ?? true;
  const pings = useNodePings(data?.nodes ?? []);

  // Публичная страница (до входа) — не называем сервис впрямую: убираем суффикс бренда.
  const bParts = (brandName || "").trim().split(/\s+/);
  const bLast = bParts[bParts.length - 1] ?? "";
  const brandMain =
    bParts.length > 1 && /^[A-Z0-9]{2,4}$/.test(bLast) ? bParts.slice(0, -1).join(" ") : brandName;

  return (
    <div className="app-scroll h-full bg-bg">
      <div className="mx-auto w-full max-w-lg px-4 py-8">
        <Link
          to="/"
          className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-fg-muted transition-colors hover:text-accent"
        >
          <ArrowLeft className="h-4 w-4" /> {t("common.back")}
        </Link>
        <div className="space-y-6">
        <div className="text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-subtle text-accent">
            <Activity className="h-6 w-6" />
          </div>
          <h1 className="text-2xl font-bold text-fg">{brandMain || "Сервис"} · {t("status.title")}</h1>
          <p className="mt-1 text-sm text-fg-muted">{t("status.autoRefresh")}</p>
        </div>

        {!loaded ? (
          <div className="flex justify-center py-16">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
          </div>
        ) : (
          <>
            <div className={`flex items-center gap-3 rounded-2xl border px-5 py-4 ${allOk ? "border-success/20 bg-success/10 text-success" : "border-warning/20 bg-warning/10 text-warning"}`}>
              {allOk ? <CheckCircle2 className="h-5 w-5" /> : <AlertTriangle className="h-5 w-5" />}
              <div className="min-w-0">
                <p className="text-sm font-semibold">{allOk ? t("info.allOk") : t("info.someDown")}</p>
                {data && data.total > 0 && (
                  <p className="text-xs opacity-80">{data.online} / {data.total} {t("status.online")}</p>
                )}
              </div>
            </div>

            {!data || data.nodes.length === 0 ? (
              <p className="py-6 text-center text-sm text-fg-subtle">{t("info.noServerData")}</p>
            ) : (
              <div className="space-y-2">
                {data.nodes.map((n, i) => (
                  <div key={i} className="rounded-2xl border border-border-subtle bg-bg-subtle px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      {n.country_code && <Flag code={n.country_code} className="h-4 w-6" />}
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{n.name}</span>
                      {n.online && n.host && pings[n.host] != null && (
                        <span className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${pingClass(pings[n.host]!)}`}>
                          {pings[n.host]} {t("status.ms")}
                        </span>
                      )}
                      <span className={`flex items-center gap-1.5 text-xs font-medium ${n.online ? "text-success" : "text-danger"}`}>
                        <span className={`h-2 w-2 rounded-full ${n.online ? "bg-success" : "bg-danger"}`} />
                        {n.online ? t("info.online") : t("info.offline")}
                      </span>
                    </div>
                    <UptimeBars history={n.history ?? []} />
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        </div>
      </div>
    </div>
  );
}
