import { useEffect, useState } from "react";
import { Activity, CheckCircle2, AlertTriangle } from "lucide-react";
import { statusApi, type StatusResponse } from "@/api/status";
import { pingClass } from "@/pages/StatusPage";
import { useNodePings } from "@/lib/ping";
import { useT } from "@/i18n/I18nContext";

// Компактный блок статуса серверов для кабинета — те же данные, что на публичной
// странице /status (включая пинг). Автообновление каждые 30 с.
export function ServerStatusCard() {
  const t = useT();
  const [data, setData] = useState<StatusResponse | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      statusApi
        .myServers()
        .then((d) => { if (alive) { setData(d); setLoaded(true); } })
        .catch(() => { if (alive) setLoaded(true); });
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const pings = useNodePings(data?.nodes ?? []);

  // Пока грузится или нет данных о нодах — блок не показываем (не мозолим глаза).
  if (!loaded || !data || data.nodes.length === 0) return null;

  const allOk = data.all_operational;

  // Самая быстрая онлайн-нода (по клиентскому пингу) — помечаем «Рекомендуем».
  let bestHost: string | null = null;
  let bestMs = Infinity;
  for (const n of data.nodes) {
    const p = n.host ? pings[n.host] : null;
    if (n.online && n.host && p != null && p < bestMs) {
      bestMs = p;
      bestHost = n.host;
    }
  }

  return (
    <div className="surface p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-accent-subtle text-accent">
            <Activity className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-fg">{t("status.title")}</p>
            <p className="text-xs text-fg-muted">
              {data.online} / {data.total} {t("status.online")}
            </p>
          </div>
        </div>
        <span className={`flex items-center gap-1.5 text-xs font-medium ${allOk ? "text-success" : "text-warning"}`}>
          {allOk ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          {allOk ? t("info.allOk") : t("info.someDown")}
        </span>
      </div>

      <div className="space-y-1.5">
        {data.nodes.map((n, i) => (
          <div key={i} className="flex items-center gap-3 rounded-xl bg-bg-subtle px-3.5 py-2.5">
            {n.country_code && (
              <img
                src={`https://flagcdn.com/h24/${n.country_code.toLowerCase()}.png`}
                alt=""
                className="h-4 w-6 flex-shrink-0 rounded-[2px] object-cover shadow-sm"
                loading="lazy"
              />
            )}
            <span className="min-w-0 truncate text-sm font-medium text-fg">{n.name}</span>
            {n.host && n.host === bestHost && (
              <span className="flex-shrink-0 rounded-md bg-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
                {t("status.recommended")}
              </span>
            )}
            <span className="flex-1" />
            {n.online && n.host && pings[n.host] != null && (
              <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${pingClass(pings[n.host]!)}`}>
                {pings[n.host]} {t("status.ms")}
              </span>
            )}
            <span className={`flex items-center gap-1.5 text-xs font-medium ${n.online ? "text-success" : "text-danger"}`}>
              <span className={`h-2 w-2 rounded-full ${n.online ? "bg-success" : "bg-danger"}`} />
              {n.online ? t("info.online") : t("info.offline")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
