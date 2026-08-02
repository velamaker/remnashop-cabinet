import { useEffect, useState } from "react";
import { Snowflake, Play, Loader2 } from "lucide-react";
import { freezeApi, type FreezeStatus } from "@/api/freeze";
import { useI18n } from "@/i18n/I18nContext";
import type { Lang } from "@/i18n/config";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const MIN_DAYS_KEY = "freeze.confirmMinDays";

/** Минимальный срок паузы в днях. Поля может не быть (наш бэкенд его не отдаёт) —
 *  тогда 0, то есть ограничения нет и подтверждение остаётся прежним. */
function minPauseDays(st: FreezeStatus): number {
  const raw = st.min_days ?? 0;
  return Number.isFinite(raw) && raw > 0 ? Math.ceil(raw) : 0;
}

/**
 * Предупреждение о минимальном сроке паузы.
 *
 * Ограничение приходит от бэкенда, который считает продление целыми сутками: снять
 * паузу раньше он не даст, потому что вернуть меньше дня ему нечем. Человек должен
 * узнать об этом ДО того, как отключит себе доступ, — иначе пауза «на часок»
 * оборачивается сутками без интернета.
 *
 * Ключа в словарях кабинета может не быть (ограничение пришло с чужим бэкендом,
 * словари живут отдельно). Сначала спрашиваем словарь — появится ключ, текст сразу
 * станет переводным; иначе показываем встроенную строку, чтобы предупреждение
 * дошло до человека в любом случае.
 */
function minDaysWarning(t: Translate, lang: Lang, days: number): string {
  const translated = t(MIN_DAYS_KEY, { days });
  if (translated !== MIN_DAYS_KEY) return translated;
  return lang === "en"
    ? `Please note: you will be able to resume no earlier than in ${days} day(s) — this service counts renewals in whole days.`
    : `Учтите: снять с паузы можно будет не раньше чем через ${days} дн. — этот сервис считает продление целыми сутками.`;
}

/**
 * Заморозка (пауза) подписки в кабинете. Показывается только если фича включена
 * админом. Заморожено → «возобновить»; активна → «заморозить». Сам ходит за статусом.
 */
export function SubscriptionFreeze() {
  const { t, lang } = useI18n();
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

  const minDays = minPauseDays(st);

  // Текст ошибки берём как есть от бэкенда: только он знает, ПОЧЕМУ отказал и когда
  // можно повторить («снять с паузы можно через N ч»). Общее «Ошибка» — лишь на
  // случай пустого сообщения, иначе человек остался бы с молчаливой кнопкой.
  const errText = (e: unknown): string =>
    (e instanceof Error && e.message.trim()) || t("freeze.err");

  const doFreeze = async () => {
    const warn = minDays > 0 ? `\n\n${minDaysWarning(t, lang, minDays)}` : "";
    if (!confirm(t("freeze.confirm") + warn)) return;
    setBusy(true);
    setErr(null);
    try {
      await freezeApi.freeze();
      await load();
    } catch (e) {
      setErr(errText(e));
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
      setErr(errText(e));
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
