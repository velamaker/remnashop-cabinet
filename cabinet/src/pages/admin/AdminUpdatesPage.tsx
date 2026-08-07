import { useEffect, useState } from "react";
import { RefreshCw, Sparkles, ExternalLink, CheckCircle2, HelpCircle, Terminal } from "lucide-react";
import { updatesAdminApi, type UpdatesInfo, type UpdateItem, type UpdateChannel } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { safeExternalUrl } from "@/lib/nav";

/** Карточка одного релиза. Разметка та же, что была на этом экране всегда, —
 *  просто вынесена, чтобы её могли показать и старый вид, и блоки. */
function ReleaseCard({ it }: { it: UpdateItem }) {
  const isNew = it.installed === false; // версия новее установленной
  return (
    <div
      className={`rounded-2xl border p-4 ${isNew ? "border-warning/40 bg-warning/8 ring-1 ring-warning/15" : "border-border-subtle bg-bg-subtle"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`rounded-md px-2 py-0.5 text-sm font-semibold ${isNew ? "bg-warning/15 text-warning" : "bg-accent/10 text-accent"}`}>
            {it.version}
          </span>
          {isNew && (
            <span className="rounded-md bg-warning/15 px-2 py-0.5 text-xs font-semibold text-warning">
              🆕 Новое · не установлено
            </span>
          )}
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
        safeExternalUrl(it.url) && (
          <a
            href={safeExternalUrl(it.url)}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
          >
            Что нового <ExternalLink className="h-3 w-3" />
          </a>
        )
      )}
    </div>
  );
}

/** Блок одной обновляемой части: кабинет или бот. Своя версия, своя лента и
 *  СВОЯ команда обновления — в этом весь смысл разделения: команда кабинета
 *  бота не обновляет, и наоборот. */
function ChannelBlock({ ch }: { ch: UpdateChannel }) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll ? ch.items : ch.items.slice(0, 5);
  const unknown = !ch.current; // версия бэкенду не видна — см. ch.note

  return (
    <section className="space-y-3">
      <div
        className={`rounded-2xl border p-4 ${
          ch.update_available
            ? "border-warning/30 bg-warning/10"
            : unknown
              ? "border-[var(--border)] bg-bg-raised"
              : "border-success/25 bg-success/8"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-fg">{ch.title}</h2>
          <span
            className={`rounded-md px-2 py-0.5 text-sm font-semibold ${unknown ? "bg-bg-subtle text-fg-subtle" : "bg-accent/10 text-accent"}`}
          >
            {ch.current ?? "версия неизвестна"}
          </span>
        </div>

        {ch.update_available ? (
          <p className="mt-2 text-sm text-fg">
            <span className="font-semibold text-warning">Доступно обновление.</span> Установлена{" "}
            <b>{ch.current}</b> → доступна <b>{ch.latest}</b>. Ниже отмечено
            <span className="font-semibold text-warning"> «Новое»</span> — что изменится после обновления.
          </p>
        ) : unknown ? (
          <p className="mt-2 flex items-start gap-2 text-sm text-fg-muted">
            <HelpCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {/* Почему не видно — говорит бэкенд в note, здесь не повторяем. */}
            {ch.latest
              ? <span>Какая версия стоит — не видно. Последняя вышедшая — <b>{ch.latest}</b>.</span>
              : <span>Какая версия стоит — не видно.</span>}
          </p>
        ) : ch.latest ? (
          <p className="mt-2 flex items-center gap-2 text-sm font-medium text-success">
            <CheckCircle2 className="h-4 w-4" />
            Установлена последняя версия ({ch.current}).
          </p>
        ) : (
          <p className="mt-2 text-sm text-fg-muted">Установлена версия {ch.current}.</p>
        )}

        {ch.note && <p className="mt-2 text-xs leading-relaxed text-fg-subtle">{ch.note}</p>}

        {(ch.commands ?? []).map((c) => (
          <div key={c.cmd} className="mt-3">
            {c.when && <p className="text-xs leading-relaxed text-fg-muted">{c.when}</p>}
            <p className="mt-1 flex items-start gap-2 overflow-x-auto rounded-lg bg-bg-raised px-3 py-2 font-mono text-xs text-fg-muted">
              <Terminal className="mt-px h-3.5 w-3.5 shrink-0" />
              <span className="whitespace-pre">{c.cmd}</span>
            </p>
          </div>
        ))}
      </div>

      {visible.map((it) => (
        <ReleaseCard key={it.version} it={it} />
      ))}
      {ch.items.length > visible.length && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full rounded-2xl border border-border-subtle bg-bg-subtle px-4 py-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
        >
          Показать все релизы ({ch.items.length})
        </button>
      )}
      {ch.items.length === 0 && (
        <p className="py-4 text-center text-sm text-fg-muted">Релизы не найдены.</p>
      )}
    </section>
  );
}

export default function AdminUpdatesPage() {
  const [data, setData] = useState<UpdatesInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = (force = false) => {
    setLoading(true);
    updatesAdminApi
      .get(force)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Не удалось загрузить обновления"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // Два блока рисуем, только если бэкенд их прислал. Наш бэкенд не присылает:
  // у него кабинет и бот — один продукт, одна версия и одна команда обновления,
  // и экран остаётся ровно таким, каким был.
  const channels = [data?.cabinet, data?.bot].filter(Boolean) as UpdateChannel[];

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-fg">
            <Sparkles className="h-5 w-5 text-accent" />
            Обновления
          </h1>
          <p className="mt-0.5 text-sm text-fg-muted">
            {channels.length > 0
              ? "Кабинет и бот обновляются отдельно — у каждого своя версия и своя команда."
              : "История релизов кабинета и админки."}
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Проверить
        </button>
      </div>

      {error && <p className="rounded-lg bg-danger/8 px-4 py-3 text-sm text-danger">{error}</p>}

      {/* Расхождение словами: что из двух отстало. Считает бэкенд — он один знает
          обе версии; экран его текст только показывает. */}
      {data?.mismatch && (
        <p className="rounded-2xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-fg">
          {data.mismatch}
        </p>
      )}

      {channels.length > 0 ? (
        <div className="space-y-6">
          {channels.map((ch) => (
            <ChannelBlock key={ch.title} ch={ch} />
          ))}
        </div>
      ) : (
        <>
          {/* Статус версии */}
          {data && (
            <div className={`rounded-2xl border p-4 ${data.update_available ? "border-warning/30 bg-warning/10" : "border-success/25 bg-success/8"}`}>
              {data.update_available ? (
                <>
                  <p className="text-sm font-semibold text-warning">Доступно обновление</p>
                  <p className="mt-1 text-sm text-fg">
                    Текущая версия <b>{data.current}</b> → доступна <b>{data.latest}</b>. Ниже отмечено
                    <span className="font-semibold text-warning"> «Новое»</span> — что изменится после обновления.
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
                <ReleaseCard key={it.version} it={it} />
              ))}
              {data && data.items.length === 0 && (
                <p className="py-8 text-center text-sm text-fg-muted">Релизы не найдены.</p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
