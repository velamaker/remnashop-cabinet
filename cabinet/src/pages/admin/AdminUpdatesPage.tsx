import { useEffect, useState } from "react";
import { RefreshCw, Sparkles, ExternalLink, CheckCircle2 } from "lucide-react";
import { updatesAdminApi, type UpdatesInfo } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";

export default function AdminUpdatesPage() {
  const [data, setData] = useState<UpdatesInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    updatesAdminApi
      .get()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Не удалось загрузить обновления"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-fg">
            <Sparkles className="h-5 w-5 text-accent" />
            Обновления
          </h1>
          <p className="mt-0.5 text-sm text-fg-muted">История релизов кабинета и админки.</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Проверить
        </button>
      </div>

      {error && <p className="rounded-lg bg-danger/8 px-4 py-3 text-sm text-danger">{error}</p>}

      {/* Статус версии */}
      {data && (
        <div className={`rounded-2xl border p-4 ${data.update_available ? "border-warning/30 bg-warning/10" : "border-success/25 bg-success/8"}`}>
          {data.update_available ? (
            <>
              <p className="text-sm font-semibold text-warning">Доступно обновление</p>
              <p className="mt-1 text-sm text-fg">
                Текущая версия <b>{data.current}</b> → доступна <b>{data.latest}</b>.
              </p>
              <p className="mt-2 rounded-lg bg-bg-raised px-3 py-2 font-mono text-xs text-fg-muted">
                cd /opt/remnashop &amp;&amp; ./update.sh
              </p>
            </>
          ) : (
            <p className="flex items-center gap-2 text-sm font-medium text-success">
              <CheckCircle2 className="h-4 w-4" />
              Установлена последняя версия ({data.current}).
            </p>
          )}
        </div>
      )}

      {loading && !data ? (
        <div className="flex justify-center py-16">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
        </div>
      ) : (
        <div className="space-y-3">
          {(data?.items ?? []).map((it) => (
            <div key={it.version} className="rounded-2xl border border-border-subtle bg-bg-subtle p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded-md bg-accent/10 px-2 py-0.5 text-sm font-semibold text-accent">
                    {it.version}
                  </span>
                  {it.name && it.name !== it.version && (
                    <span className="text-sm font-medium text-fg">{it.name}</span>
                  )}
                </div>
                {it.date && <span className="text-xs text-fg-subtle">{formatDate(it.date)}</span>}
              </div>
              {it.notes ? (
                <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-fg-muted">
                  {it.notes}
                </pre>
              ) : (
                it.url && (
                  <a
                    href={it.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
                  >
                    Что нового <ExternalLink className="h-3 w-3" />
                  </a>
                )
              )}
            </div>
          ))}
          {data && data.items.length === 0 && (
            <p className="py-8 text-center text-sm text-fg-muted">Релизы не найдены.</p>
          )}
        </div>
      )}
    </div>
  );
}
