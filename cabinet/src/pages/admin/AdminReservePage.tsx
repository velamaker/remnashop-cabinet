import { useCallback, useEffect, useState } from "react";
import { Umbrella, RefreshCw, AlertTriangle } from "lucide-react";
import { reserveAdminApi, type ReserveGrants } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { ReserveCard } from "./AdminSettingsPage";

// «Резервный доступ истёкшим» — вынесен из «Настроек».
export default function AdminReservePage() {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Umbrella className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Резервный доступ истёкшим</h1>
      </div>
      <ReserveCard />
      <ReserveGrantsCard />
    </div>
  );
}

/**
 * Кто сейчас на резерве — и работает ли он у них НА САМОМ ДЕЛЕ.
 *
 * Без этого экрана дефект «выдан, но в приложении пусто» не видно вообще: своя
 * таблица говорит «резерв выдавали», а подписка отдаёт серверы через сквады, и
 * ACTIVE без сквадов — это выданный резерв, которым нельзя пользоваться. Поэтому
 * бэкенд подмешивает живое состояние панели, а строки с проблемой поднимаются наверх.
 *
 * У адаптера поверх чужого бота такой ручки нет (у него резерв — своя механика),
 * поэтому 404/501 прячут блок целиком, как и в остальных карточках админки.
 */
function ReserveGrantsCard() {
  const [data, setData] = useState<ReserveGrants | null>(null);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    reserveAdminApi
      .grants()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (e instanceof ApiError && (e.status === 404 || e.status === 501)) setHidden(true);
        else setError(e instanceof ApiError ? e.detail : "Не удалось загрузить");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (hidden) return null;

  const items = data?.items ?? [];
  // Сначала то, что требует внимания: сломанные резервы, потом действующие.
  const sorted = [...items].sort((a, b) => {
    const weight = (x: typeof a) => (x.problem ? 0 : x.ended ? 2 : 1);
    return weight(a) - weight(b);
  });

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-fg">Кто на резерве</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Состояние берётся из панели, а не из нашей таблицы: подписка отдаёт серверы через
            сквады, поэтому «активен без сквадов» — это выданный резерв, которым нельзя
            пользоваться.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="shrink-0 rounded-xl border border-border-subtle px-3 py-2 text-xs text-fg-muted hover:text-fg disabled:opacity-60"
          aria-label="Обновить"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}

      {!error && data && (
        <>
          <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
            <span>
              На резерве сейчас: <b className="text-fg">{data.active}</b>
            </span>
            {data.broken > 0 && (
              <span className="inline-flex items-center gap-1 text-warning">
                <AlertTriangle className="h-3.5 w-3.5" />
                не работает: <b>{data.broken}</b>
              </span>
            )}
          </div>

          {sorted.length === 0 ? (
            <p className="text-xs text-fg-muted">Резерв пока никому не выдавался.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead className="text-fg-muted">
                  <tr>
                    <th className="py-1.5 pr-3 font-medium">Клиент</th>
                    <th className="py-1.5 pr-3 font-medium">Выдан</th>
                    <th className="py-1.5 pr-3 font-medium">До</th>
                    <th className="py-1.5 pr-3 font-medium">В панели</th>
                    <th className="py-1.5 pr-3 font-medium">Сквады</th>
                    <th className="py-1.5 font-medium">Трафик</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((g) => (
                    <tr key={g.user_id} className="border-t border-border-subtle align-top">
                      <td className="py-2 pr-3">
                        <div className="text-fg">{g.username || `id ${g.user_id}`}</div>
                        {g.telegram_id && <div className="text-fg-muted">tg {g.telegram_id}</div>}
                        {g.problem && (
                          <div className="mt-1 inline-flex items-start gap-1 text-warning">
                            <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
                            <span>{g.problem}</span>
                          </div>
                        )}
                        {!g.problem && g.note && <div className="mt-1 text-fg-muted">{g.note}</div>}
                      </td>
                      <td className="py-2 pr-3 text-fg-muted">
                        {g.granted_at ? formatDate(g.granted_at) : "—"}
                      </td>
                      <td className="py-2 pr-3 text-fg-muted">
                        {g.ended ? "закончился" : g.reserve_expire_at ? formatDate(g.reserve_expire_at) : "—"}
                      </td>
                      <td className="py-2 pr-3 text-fg-muted">{g.panel?.status ?? "—"}</td>
                      <td className="py-2 pr-3 text-fg-muted">
                        {g.panel ? (g.panel.squads.length ? g.panel.squads.join(", ") : "нет") : "—"}
                      </td>
                      <td className="py-2 text-fg-muted">
                        {g.panel ? `${g.panel.used_traffic_gb} / ${g.panel.traffic_limit_gb} ГБ` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
