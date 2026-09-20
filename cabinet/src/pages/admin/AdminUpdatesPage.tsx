import { useEffect, useState } from "react";
import { RefreshCw, Sparkles, ExternalLink, CheckCircle2, HelpCircle, Terminal } from "lucide-react";
import { updatesAdminApi, type UpdatesInfo, type UpdateItem, type UpdateChannel } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { safeExternalUrl } from "@/lib/nav";
import { useBranding } from "@/contexts/BrandingContext";
import { BOT_CAPABILITIES, missingBotCaps } from "@/lib/botCapabilities";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

// Одна фраза — один ключ: номера версий <b>…</b> и жёлтые акценты <warn>…</warn>
// живут ВНУТРИ перевода. Так переводчик сам решает порядок слов и что выделить, а
// предложение не собирается из кусков.
function withMarkup(s: string) {
  return s.split(/(<b>.*?<\/b>|<warn>.*?<\/warn>)/g).map((part, i) =>
    part.startsWith("<b>") ? (
      <b key={i}>{part.slice(3, -4)}</b>
    ) : part.startsWith("<warn>") ? (
      <span key={i} className="font-semibold text-warning">
        {part.slice(6, -7)}
      </span>
    ) : (
      part
    ),
  );
}

/** Карточка одного релиза. Разметка та же, что была на этом экране всегда, —
 *  просто вынесена, чтобы её могли показать и старый вид, и блоки. */
function ReleaseCard({ it }: { it: UpdateItem }) {
  const t = useT();
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
              {t("adm.updates.badge_new")}
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
            {t("adm.updates.whats_new")} <ExternalLink className="h-3 w-3" />
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
  const t = useT();
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
            {ch.current ?? t("adm.updates.version_unknown")}
          </span>
        </div>

        {ch.update_available ? (
          <p className="mt-2 text-sm text-fg">
            {withMarkup(
              t("adm.updates.update_note", { current: ch.current ?? "", latest: ch.latest ?? "" }),
            )}
          </p>
        ) : unknown ? (
          <p className="mt-2 flex items-start gap-2 text-sm text-fg-muted">
            <HelpCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {/* Почему не видно — говорит бэкенд в note, здесь не повторяем. */}
            {ch.latest
              ? <span>{withMarkup(t("adm.updates.unknown_with_latest", { latest: ch.latest }))}</span>
              : <span>{t("adm.updates.unknown")}</span>}
          </p>
        ) : ch.latest ? (
          <p className="mt-2 flex items-center gap-2 text-sm font-medium text-success">
            <CheckCircle2 className="h-4 w-4" />
            {t("adm.updates.up_to_date", { version: ch.current ?? "" })}
          </p>
        ) : (
          <p className="mt-2 text-sm text-fg-muted">
            {t("adm.updates.installed_version", { version: ch.current ?? "" })}
          </p>
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
          {t("adm.updates.show_all", { n: ch.items.length })}
        </button>
      )}
      {ch.items.length === 0 && (
        <p className="py-4 text-center text-sm text-fg-muted">{t("adm.updates.no_releases")}</p>
      )}
    </section>
  );
}

export default function AdminUpdatesPage() {
  const t = useT();
  const [data, setData] = useState<UpdatesInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // translate, а не t: иначе смена языка пересоздавала бы load и перезапрашивала
  // обновления (а в .catch язык всё равно берётся актуальный, модульно).
  const load = (force = false) => {
    setLoading(true);
    updatesAdminApi
      .get(force)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.detail : translate("adm.updates.load_error")))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  // Кабинет новее бота (обновили только кабинет или кабинет на отдельном сервере):
  // часть функций спрятана, и именно здесь владелец ищет, почему. Считаем только по
  // свежему оформлению — в кэше списка возможностей нет, и карточка мигала бы у
  // всех. Поверх чужого бэкенда список пуст: там решает адаптер.
  const { appearance, loaded } = useBranding();
  const missing = loaded ? missingBotCaps(appearance) : [];

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
            {t("adm.updates.title")}
          </h1>
          <p className="mt-0.5 text-sm text-fg-muted">
            {channels.length > 0
              ? t("adm.updates.subtitle_channels")
              : t("adm.updates.subtitle")}
          </p>
        </div>
        <button
          onClick={() => load(true)}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {t("adm.updates.check")}
        </button>
      </div>

      {error && <p className="rounded-lg bg-danger/8 px-4 py-3 text-sm text-danger">{error}</p>}

      {missing.length > 0 && (
        <section
          data-testid="bot-behind-cabinet"
          className="rounded-2xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-fg"
        >
          <p className="font-semibold text-warning">{t("adm.updates.bot_behind_title")}</p>
          <p className="mt-1 text-fg-muted">{t("adm.updates.bot_behind_intro")}</p>
          <ul className="mt-2 list-disc space-y-0.5 pl-5">
            {/* Подписи — из манифеста возможностей бота: его строки читает sed'ом
                update.sh, поэтому они живут там, а не в словаре кабинета. */}
            {missing.map((cap) => (
              <li key={cap}>{BOT_CAPABILITIES[cap].label}</li>
            ))}
          </ul>
          <p className="mt-2 text-fg-muted">{t("adm.updates.bot_behind_howto")}</p>
          <p className="mt-1 flex items-start gap-2 overflow-x-auto rounded-lg bg-bg-raised px-3 py-2 font-mono text-xs text-fg-muted">
            <Terminal className="mt-px h-3.5 w-3.5 shrink-0" />
            <span className="whitespace-pre">./update.sh --with-bot</span>
          </p>
        </section>
      )}

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
                  <p className="text-sm font-semibold text-warning">{t("adm.updates.update_available_title")}</p>
                  <p className="mt-1 text-sm text-fg">
                    {withMarkup(
                      t("adm.updates.update_available_body", {
                        current: data.current,
                        latest: data.latest ?? "",
                      }),
                    )}
                  </p>
                  <p className="mt-2 rounded-lg bg-bg-raised px-3 py-2 font-mono text-xs text-fg-muted">
                    cd /opt/remnashop &amp;&amp; ./update.sh
                  </p>
                </>
              ) : (
                <p className="flex items-center gap-2 text-sm font-medium text-success">
                  <CheckCircle2 className="h-4 w-4" />
                  {t("adm.updates.up_to_date", { version: data.current })}
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
                <p className="py-8 text-center text-sm text-fg-muted">{t("adm.updates.no_releases")}</p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
