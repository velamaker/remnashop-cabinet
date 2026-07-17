import { useEffect, useState } from "react";
import { Snowflake, Play, Loader2 } from "lucide-react";
import { freezeApi, type FreezeStatus } from "@/api/freeze";
import { useT } from "@/i18n/I18nContext";

/**
 * Заморозка (пауза) подписки в кабинете. Показывается только если фича включена
 * админом. Заморожено → «возобновить»; активна → «заморозить». Сам ходит за статусом.
 */
export function SubscriptionFreeze() {
  const t = useT();
  const [st, setSt] = useState<FreezeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    freezeApi
      .status()
      .then(setSt)
      .catch(() => setSt(null));

  useEffect(() => {
    load();
  }, []);

  if (!st?.enabled) return null;
  if (!st.frozen && !st.can_freeze) return null;

  const doFreeze = async () => {
    if (!confirm(t("freeze.confirm"))) return;
    setBusy(true);
    setErr(null);
    try {
      await freezeApi.freeze();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("freeze.err"));
    } finally {
      setBusy(false);
    }
  };

  const doUnfreeze = async () => {
    setBusy(true);
    setErr(null);
    try {
      await freezeApi.unfreeze();
      await load();
      // срок мог сдвинуться — обновим страницу
      setTimeout(() => window.location.reload(), 400);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("freeze.err"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-4 sm:p-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-sky-500/15 text-sky-500">
          <Snowflake className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          {st.frozen ? (
            <>
              <p className="text-base font-bold text-fg">{t("freeze.pausedTitle")}</p>
              <p className="mt-0.5 text-xs text-fg-muted sm:text-sm">
                {t("freeze.pausedDesc", { days: st.remaining_days ?? 0 })}
              </p>
            </>
          ) : (
            <>
              <p className="text-base font-bold text-fg">{t("freeze.title")}</p>
              <p className="mt-0.5 text-xs text-fg-muted sm:text-sm">
                {t("freeze.desc", { max: st.max_days ?? 0, left: st.days_left ?? 0 })}
              </p>
            </>
          )}
          {err && <p className="mt-1 text-xs text-danger">{err}</p>}
        </div>
        <button
          type="button"
          onClick={st.frozen ? doUnfreeze : doFreeze}
          disabled={busy}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-sky-500 px-3 py-2 text-sm font-semibold text-white hover:bg-sky-500/90 disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : st.frozen ? <Play className="h-4 w-4" /> : <Snowflake className="h-4 w-4" />}
          {st.frozen ? t("freeze.resume") : t("freeze.freeze")}
        </button>
      </div>
    </div>
  );
}
