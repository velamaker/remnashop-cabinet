/**
 * «Пользователи» → «Массово по фильтру»: добавить дни подписки и написать сообщение
 * ровно отфильтрованным людям, плюс панель фоновых задач.
 *
 * Запуск в три шага: предпросмотр (бэкенд считает, кто получит и кто нет, и почему)
 * → подтверждение → фоновая задача. Ничего не уходит людям, пока админ не нажал
 * «Да, …» на втором экране.
 *
 * ДВОЙНОЙ КЛИК И ПОВТОР. У каждого набора параметров свой request_id: двойное нажатие
 * отсекается ещё здесь, а повтор после обрыва сети уходит с тем же идентификатором —
 * бэкенд узнаёт запуск и не создаёт вторую задачу. Сменили дни, галочки или фильтр —
 * это уже другой запуск и новый идентификатор.
 *
 * Бэкенд без этих ручек («Бедолага» старее кабинета) отвечает 501 — пункт прячется
 * через `onUnsupported`, как остальные органы управления страницы.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import {
  bulkJobsAdminApi,
  type BulkChannel,
  type BulkDaysPreview,
  type BulkFilters,
  type BulkJob,
  type BulkJobItem,
  type BulkMessagePreview,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { useI18n, useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";
import { pluralFor } from "@/lib/pluralRu";
import {
  compensationText,
  estimateMinutes,
  isActive,
  jobCounters,
  jobKindLabel,
  jobStatusLabel,
  newRequestId,
  skippedLines,
} from "@/lib/bulkJobs";

const TEXT_MAX = 4000;
const POLL_MS = 3000;
const ALL_CHANNELS: BulkChannel[] = ["telegram", "cabinet", "email"];

const BUTTON =
  "inline-flex items-center justify-center rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-fg hover:bg-bg-subtle disabled:opacity-40 transition-colors";
const PRIMARY =
  "inline-flex items-center justify-center rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-40 transition-colors";
const INPUT =
  "h-9 w-full rounded-lg border border-[var(--border)] bg-bg px-3 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

// «3 дня» и «3 days»: форму счётного слова выбирает pluralFor ПО ЯЗЫКУ, сам текст —
// из словаря. Через pluralRu на английском выходило бы «21 day».
function daysLabel(t: Translate, lang: string, n: number): string {
  return t(pluralFor(lang, n, "adm.bulk.days_one", "adm.bulk.days_few", "adm.bulk.days_many"), { n });
}

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);
const isUnsupported = (e: unknown) => e instanceof ApiError && e.status === 501;

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Порталом в body, как карточка пользователя: внутри .app-scroll на iOS
  // position:fixed запирается в слое прокрутки и уезжает под верхнюю панель.
  return createPortal(
    <div
      onClick={onClose}
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-3 pt-[max(1.5rem,env(safe-area-inset-top))] sm:p-4 sm:pt-8"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[90dvh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-bg shadow-raised"
      >
        <div className="flex flex-shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <p className="text-sm font-semibold text-fg">{title}</p>
          <button onClick={onClose} aria-label={t("adm.bulk.close")} className="-m-1 rounded-lg p-2 text-fg-muted hover:text-fg">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm text-fg">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm text-fg">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-[var(--accent)]" />
      <span>{children}</span>
    </label>
  );
}

function Spinner() {
  return <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />;
}

function ChannelPicker({ value, onChange }: { value: BulkChannel[]; onChange: (v: BulkChannel[]) => void }) {
  const t = useT();
  const toggle = (c: BulkChannel, on: boolean) =>
    onChange(ALL_CHANNELS.filter((x) => (x === c ? on : value.includes(x))));
  return (
    <div className="space-y-1.5">
      <Check checked={value.includes("telegram")} onChange={(v) => toggle("telegram", v)}>
        Telegram
      </Check>
      <Check checked={value.includes("cabinet")} onChange={(v) => toggle("cabinet", v)}>
        {t("adm.bulk.ch_cabinet")}
      </Check>
      <Check checked={value.includes("email")} onChange={(v) => toggle("email", v)}>
        {t("adm.bulk.ch_email")}
      </Check>
    </div>
  );
}

/** Ключ фильтров для зависимостей эффектов: объект с новой ссылкой на каждом рендере
 *  страницы иначе перезапрашивал бы предпросмотр без всякой причины. */
function useStableFilters<T>(filters: T): [T, string] {
  const key = JSON.stringify(filters);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stable = useMemo(() => filters, [key]);
  return [stable, key];
}

// ─── «Добавить дни подписки» ────────────────────────────────────────────────

export function BulkDaysDialog({
  filters: rawFilters,
  onClose,
  onStarted,
  onUnsupported,
}: {
  filters: BulkFilters;
  onClose: () => void;
  onStarted: (jobId: number) => void;
  onUnsupported: (key: string) => void;
}) {
  const { t, lang } = useI18n();
  const tr = useRef(t);
  tr.current = t;
  const [filters, filtersKey] = useStableFilters(rawFilters);
  const [days, setDays] = useState("3");
  const [includeTrial, setIncludeTrial] = useState(false);
  const [includeLimited, setIncludeLimited] = useState(false);
  const [notify, setNotify] = useState(false);
  const [text, setText] = useState(() => compensationText(3, lang));
  const [textTouched, setTextTouched] = useState(false);
  const [channels, setChannels] = useState<BulkChannel[]>(ALL_CHANNELS);
  const [preview, setPreview] = useState<BulkDaysPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<"form" | "confirm">("form");
  const [allowRepeat, setAllowRepeat] = useState(false);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const requestId = useRef(newRequestId());
  const unsupported = useRef(onUnsupported);
  unsupported.current = onUnsupported;

  const daysNum = Number(days);
  const daysValid = /^\d+$/.test(days) && daysNum >= 1 && daysNum <= 365;

  // Новый набор параметров — новый запуск.
  useEffect(() => {
    requestId.current = newRequestId();
    setAllowRepeat(false);
  }, [days, includeTrial, includeLimited, notify, text, channels, filtersKey]);

  // Текст компенсации следует за числом дней, пока админ его не правил.
  useEffect(() => {
    if (!textTouched && daysValid) setText(compensationText(daysNum, lang));
  }, [daysNum, daysValid, textTouched, lang]);

  useEffect(() => {
    if (!daysValid) {
      setPreview(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      bulkJobsAdminApi
        .daysPreview(filters, { days: daysNum, include_trial: includeTrial, include_limited: includeLimited })
        .then((p) => {
          if (!cancelled) {
            setPreview(p);
            setError(null);
          }
        })
        .catch((e) => {
          if (cancelled) return;
          setPreview(null);
          if (isUnsupported(e)) unsupported.current("users.bulk.days");
          else setError(errorText(e, tr.current("adm.bulk.err_preview_days")));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [filters, daysNum, daysValid, includeTrial, includeLimited, reload]);

  const eligible = preview ? preview.apply + preview.apply_frozen + preview.deferred : 0;
  const notifyValid = !notify || (text.trim().length > 0 && text.length <= TEXT_MAX && channels.length > 0);
  const canNext = !!preview && !loading && daysValid && eligible > 0 && notifyValid;
  const repeat = preview?.recently_extended;
  const needsRepeat = !!repeat && repeat.count > 0;

  const start = async () => {
    if (!preview || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const res = await bulkJobsAdminApi.startDays(filters, {
        days: daysNum,
        include_trial: includeTrial,
        include_limited: includeLimited,
        allow_repeat: allowRepeat,
        segment_hash: preview.segment_hash,
        expected_apply: eligible,
        request_id: requestId.current,
        notify: notify ? { text: text.trim(), channels } : null,
      });
      onStarted(res.job_id);
    } catch (e) {
      if (isUnsupported(e)) {
        onUnsupported("users.bulk.days");
      } else if (e instanceof ApiError && e.status === 409 && e.detail.startsWith("Выборка изменилась")) {
        setError(t("adm.bulk.err_segment_changed"));
        setStep("form");
        setReload((n) => n + 1);
      } else {
        setError(errorText(e, t("adm.bulk.err_start")));
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  if (step === "confirm" && preview) {
    return (
      <Modal title={t("adm.bulk.days_title")} onClose={onClose}>
        <p className="font-medium">
          {t(
            pluralFor(lang, eligible, "adm.bulk.confirm_one", "adm.bulk.confirm_few", "adm.bulk.confirm_many"),
            { days: daysLabel(t, lang, daysNum), n: eligible },
          )}
        </p>
        <p className="text-fg-muted">{t("adm.bulk.confirm_note", { n: estimateMinutes("days", eligible) })}</p>
        {needsRepeat && repeat && (
          <div className="space-y-2 rounded-lg bg-warning/10 px-3 py-2">
            <p className="text-warning">
              {t("adm.bulk.repeat_warn", { n: repeat.count, job: repeat.job_id ?? "", days: repeat.days ?? "" })}
            </p>
            <Check checked={allowRepeat} onChange={setAllowRepeat}>
              {t("adm.bulk.repeat_ack_days")}
            </Check>
          </div>
        )}
        {error && <p className="text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} onClick={() => setStep("form")} disabled={busy}>
            {t("adm.bulk.back")}
          </button>
          <button className={PRIMARY} onClick={start} disabled={busy || (needsRepeat && !allowRepeat)}>
            {busy ? t("adm.bulk.starting") : t("adm.bulk.yes_add")}
          </button>
        </div>
      </Modal>
    );
  }

  const lines = skippedLines(preview?.skipped);
  return (
    <Modal title={t("adm.bulk.days_title")} onClose={onClose}>
      <div>
        <label htmlFor="bulk-days" className="mb-1 block text-xs text-fg-muted">
          {t("adm.bulk.days_label")}
        </label>
        <input
          id="bulk-days"
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={(e) => setDays(e.target.value)}
          className={INPUT}
        />
        <p className={`mt-1 text-xs ${daysValid ? "text-fg-subtle" : "text-danger"}`}>{t("adm.bulk.days_range")}</p>
      </div>
      <div className="space-y-1.5">
        <Check checked={includeTrial} onChange={setIncludeTrial}>
          {t("adm.bulk.include_trial")}
        </Check>
        <Check checked={includeLimited} onChange={setIncludeLimited}>
          {t("adm.bulk.include_limited")}
        </Check>
        {includeLimited && (
          <p className="pl-6 text-xs text-warning">{t("adm.bulk.limited_warn")}</p>
        )}
      </div>

      <div className="space-y-1 rounded-lg border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
        <p className="text-xs font-medium text-fg-muted">{t("adm.bulk.affected")}</p>
        {loading ? (
          <Spinner />
        ) : preview ? (
          <>
            <p>
              {t(
                pluralFor(lang, preview.apply, "adm.bulk.apply_one", "adm.bulk.apply_few", "adm.bulk.apply_many"),
                { days: daysLabel(t, lang, daysNum), n: preview.apply },
              )}
            </p>
            {preview.apply_frozen > 0 && <p>{t("adm.bulk.frozen", { n: preview.apply_frozen })}</p>}
            {preview.deferred > 0 && <p>{t("adm.bulk.deferred", { n: preview.deferred })}</p>}
            <p className="text-xs text-fg-muted">{t("adm.bulk.verify_note")}</p>
            {lines.length > 0 && (
              <div className="pt-1">
                <p className="text-xs font-medium text-fg-muted">{t("adm.bulk.skipped_title")}</p>
                <ul className="list-disc pl-5 text-xs text-fg-muted">
                  {lines.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </div>
            )}
            {preview.active_job_id != null && (
              <p className="text-xs text-warning">{t("adm.bulk.active_job", { n: preview.active_job_id })}</p>
            )}
          </>
        ) : null}
      </div>

      <div className="space-y-2">
        <Check checked={notify} onChange={setNotify}>
          {t("adm.bulk.notify")}
        </Check>
        {notify && (
          <div className="space-y-2 pl-6">
            <textarea
              aria-label={t("adm.bulk.text_aria")}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setTextTouched(true);
              }}
              rows={5}
              className="w-full rounded-lg border border-[var(--border)] bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className={`text-right text-xs ${text.length > TEXT_MAX ? "text-danger" : "text-fg-subtle"}`}>
              {text.length}/{TEXT_MAX}
            </p>
            <ChannelPicker value={channels} onChange={setChannels} />
          </div>
        )}
      </div>

      {error && <p className="text-danger">{error}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button className={BUTTON} onClick={onClose}>
          {t("adm.bulk.cancel")}
        </button>
        <button className={PRIMARY} onClick={() => setStep("confirm")} disabled={!canNext}>
          {t("adm.bulk.next")}
        </button>
      </div>
    </Modal>
  );
}

// ─── «Сообщение отфильтрованным» ────────────────────────────────────────────

function testResultText(r: { telegram: boolean; reason: string | null }): string {
  if (r.telegram) return translate("adm.bulk.test_ok");
  if (r.reason === "no_telegram") return translate("adm.bulk.test_no_tg");
  return translate("adm.bulk.test_failed");
}

export function BulkMessageDialog({
  filters: rawFilters,
  sourceJobId = null,
  onClose,
  onStarted,
  onUnsupported,
}: {
  filters: BulkFilters | null;
  sourceJobId?: number | null;
  onClose: () => void;
  onStarted: (jobId: number) => void;
  onUnsupported: (key: string) => void;
}) {
  const { t, lang } = useI18n();
  const tr = useRef(t);
  tr.current = t;
  const [filters, filtersKey] = useStableFilters(rawFilters);
  const [text, setText] = useState("");
  const [channels, setChannels] = useState<BulkChannel[]>(ALL_CHANNELS);
  const [preview, setPreview] = useState<BulkMessagePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<"form" | "confirm">("form");
  const [repeatAsked, setRepeatAsked] = useState<string | null>(null);
  const [allowRepeat, setAllowRepeat] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testNote, setTestNote] = useState<string | null>(null);
  const sending = useRef(false);
  const requestId = useRef(newRequestId());
  const unsupported = useRef(onUnsupported);
  unsupported.current = onUnsupported;
  const channelsKey = channels.join(",");

  useEffect(() => {
    requestId.current = newRequestId();
    setAllowRepeat(false);
    setRepeatAsked(null);
  }, [text, channelsKey, filtersKey, sourceJobId]);

  useEffect(() => {
    if (!channelsKey) {
      setPreview(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    bulkJobsAdminApi
      .messagePreview(sourceJobId != null ? null : filters, {
        channels: channelsKey.split(",") as BulkChannel[],
        source_job_id: sourceJobId,
      })
      .then((p) => {
        if (!cancelled) {
          setPreview(p);
          setError(null);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setPreview(null);
        if (isUnsupported(e)) unsupported.current("users.bulk.message");
        else setError(errorText(e, tr.current("adm.bulk.err_preview_msg")));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters, channelsKey, sourceJobId, reload]);

  const textValid = text.trim().length > 0 && text.length <= TEXT_MAX;
  const canNext = !!preview && !loading && preview.recipients > 0 && channels.length > 0 && textValid;

  const sendTest = async () => {
    setTesting(true);
    setTestNote(null);
    try {
      setTestNote(testResultText(await bulkJobsAdminApi.testMessage(text)));
    } catch (e) {
      setTestNote(errorText(e, t("adm.bulk.err_test")));
    } finally {
      setTesting(false);
    }
  };

  const start = async () => {
    if (!preview || sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      const res = await bulkJobsAdminApi.startMessage(sourceJobId != null ? null : filters, {
        text: text.trim(),
        channels,
        source_job_id: sourceJobId,
        segment_hash: preview.segment_hash,
        expected_recipients: preview.recipients,
        request_id: requestId.current,
        allow_repeat: allowRepeat,
      });
      onStarted(res.job_id);
    } catch (e) {
      if (isUnsupported(e)) {
        onUnsupported("users.bulk.message");
      } else if (e instanceof ApiError && e.status === 409 && e.detail.includes("уже получили это же сообщение")) {
        setRepeatAsked(e.detail);
      } else if (e instanceof ApiError && e.status === 409 && e.detail.startsWith("Выборка изменилась")) {
        setError(t("adm.bulk.err_segment_changed"));
        setStep("form");
        setReload((n) => n + 1);
      } else {
        setError(errorText(e, t("adm.bulk.err_start")));
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  if (step === "confirm" && preview) {
    return (
      <Modal title={t("adm.bulk.msg_title")} onClose={onClose}>
        <p className="font-medium">
          {t(
            pluralFor(lang, preview.recipients, "adm.bulk.msg_confirm_one", "adm.bulk.msg_confirm_few", "adm.bulk.msg_confirm_many"),
            { n: preview.recipients },
          )}
        </p>
        <p className="text-fg-muted">{t("adm.bulk.msg_no_recall")}</p>
        {repeatAsked && (
          <div className="space-y-2 rounded-lg bg-warning/10 px-3 py-2">
            <p className="text-warning">{repeatAsked}</p>
            <Check checked={allowRepeat} onChange={setAllowRepeat}>
              {t("adm.bulk.repeat_ack_msg")}
            </Check>
          </div>
        )}
        {error && <p className="text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} onClick={() => setStep("form")} disabled={busy}>
            {t("adm.bulk.back")}
          </button>
          <button className={PRIMARY} onClick={start} disabled={busy || (!!repeatAsked && !allowRepeat)}>
            {busy ? t("adm.bulk.sending") : t("adm.bulk.yes_send")}
          </button>
        </div>
      </Modal>
    );
  }

  const ch = preview?.by_channel;
  return (
    <Modal title={t("adm.bulk.msg_title")} onClose={onClose}>
      {sourceJobId != null && <p className="text-xs text-fg-muted">{t("adm.bulk.from_job", { n: sourceJobId })}</p>}
      <div>
        <textarea
          aria-label={t("adm.bulk.text_aria")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          placeholder={t("adm.bulk.text_ph")}
          className="w-full rounded-lg border border-[var(--border)] bg-bg px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <p className={`text-right text-xs ${text.length > TEXT_MAX ? "text-danger" : "text-fg-subtle"}`}>
          {text.length}/{TEXT_MAX}
        </p>
      </div>
      <ChannelPicker value={channels} onChange={setChannels} />

      <div className="space-y-1 rounded-lg border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
        {loading ? (
          <Spinner />
        ) : preview && ch ? (
          <>
            <p className="font-medium">{t("adm.bulk.recipients", { n: preview.recipients })}</p>
            <p className="text-xs text-fg-muted">{t("adm.bulk.ch_tg", { n: ch.telegram })}</p>
            {ch.telegram_bot_blocked > 0 && (
              <p className="text-xs text-fg-muted">{t("adm.bulk.ch_blocked", { n: ch.telegram_bot_blocked })}</p>
            )}
            <p className="text-xs text-fg-muted">
              {t("adm.bulk.ch_no_tg", { push: ch.push_only, email: ch.email_only, cabinet: ch.cabinet_only })}
            </p>
            {ch.unreachable > 0 && <p className="text-xs text-warning">{t("adm.bulk.ch_unreachable", { n: ch.unreachable })}</p>}
            {(preview.skipped.BLOCKED ?? 0) > 0 && (
              <p className="text-xs text-fg-muted">{t("adm.bulk.skipped_blocked", { n: preview.skipped.BLOCKED ?? 0 })}</p>
            )}
            {channels.includes("email") && !preview.email_enabled && (
              <p className="text-xs text-warning">{t("adm.bulk.email_off")}</p>
            )}
            {preview.active_job_id != null && (
              <p className="text-xs text-warning">{t("adm.bulk.active_broadcast", { n: preview.active_job_id })}</p>
            )}
          </>
        ) : channels.length === 0 ? (
          <p className="text-xs text-fg-muted">{t("adm.bulk.pick_channel")}</p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className={BUTTON} onClick={sendTest} disabled={testing || !textValid}>
          {testing ? t("adm.bulk.sending") : t("adm.bulk.test_btn")}
        </button>
        {testNote && <span className="text-xs text-fg-muted">{testNote}</span>}
      </div>

      {error && <p className="text-danger">{error}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button className={BUTTON} onClick={onClose}>
          {t("adm.bulk.cancel")}
        </button>
        <button className={PRIMARY} onClick={() => setStep("confirm")} disabled={!canNext}>
          {t("adm.bulk.next")}
        </button>
      </div>
    </Modal>
  );
}

// ─── «Фоновые задачи» ───────────────────────────────────────────────────────

function JobItems({ job }: { job: BulkJob }) {
  const t = useT();
  const tr = useRef(t);
  tr.current = t;
  const [items, setItems] = useState<BulkJobItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const missed = await bulkJobsAdminApi.items(job.id, ["FAILED", "SKIPPED", "UNKNOWN"], 200);
      let flagged: BulkJobItem[] = [];
      if (job.verify_flagged > 0) {
        const done = await bulkJobsAdminApi.items(job.id, ["DONE"], 500);
        flagged = done.items.filter((i) => i.verify_note);
      }
      return [...flagged, ...missed.items];
    };
    load()
      .then((list) => {
        if (!cancelled) setItems(list);
      })
      .catch((e) => {
        if (!cancelled) setError(errorText(e, tr.current("adm.bulk.err_items")));
      });
    return () => {
      cancelled = true;
    };
  }, [job.id, job.verify_flagged, job.done]);

  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!items) return <Spinner />;
  if (items.length === 0) return <p className="text-xs text-fg-muted">{t("adm.bulk.all_delivered")}</p>;
  return (
    <ul className="max-h-60 space-y-1 overflow-y-auto text-xs">
      {items.map((i, n) => (
        <li key={`${i.user_id ?? "x"}-${n}`} className="flex flex-wrap gap-x-2">
          <span className="font-medium text-fg">{i.name ?? "—"}</span>
          <span className="text-fg-muted">{i.reason ?? i.error ?? i.status}</span>
        </li>
      ))}
    </ul>
  );
}

export function BulkJobsPanel({
  fullAccess,
  readonly,
  refreshKey,
  onWriteRecipients,
  onUnsupported,
}: {
  fullAccess: boolean;
  readonly: boolean;
  refreshKey: number;
  onWriteRecipients: (jobId: number) => void;
  onUnsupported: (key: string) => void;
}) {
  const { t, lang } = useI18n();
  const [jobs, setJobs] = useState<BulkJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const unsupported = useRef(onUnsupported);
  unsupported.current = onUnsupported;
  const canManage = fullAccess && !readonly;
  const tr = useRef(t);
  tr.current = t;

  const load = useCallback(() => {
    bulkJobsAdminApi
      .jobs(10)
      .then(async (r) => {
        // Список — десять последних задач, а идущая или приостановленная могла из них
        // выпасть (пока панель лежала, отправили десяток сообщений). Без неё нет ни
        // «Остановить», ни «Продолжить», а новый запуск упирается в «Уже идёт задача
        // №N». Такую задачу догружаем отдельно и показываем первой.
        const shown = new Set(r.items.map((j) => j.id));
        const missing = [r.active?.days, r.active?.message].filter(
          (id): id is number => typeof id === "number" && !shown.has(id),
        );
        const extra = await Promise.all(missing.map((id) => bulkJobsAdminApi.job(id).catch(() => null)));
        setJobs([...extra.filter((j): j is BulkJob => j != null), ...r.items]);
        setError(null);
      })
      .catch((e) => {
        // Панель рисуется сама, без нажатия, и видна всем админам: бэкенд, у которого
        // этой ручки нет вовсе (501 адаптера или 404 бота старее кабинета), — не
        // ошибка на странице, а просто отсутствие журнала.
        if (isUnsupported(e) || (e instanceof ApiError && e.status === 404)) unsupported.current("users.bulk.jobs");
        else setError(errorText(e, tr.current("adm.bulk.err_jobs")));
      });
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Опрашиваем, только пока есть что-то идущее: готовые задачи не меняются.
  const polling = jobs.some((j) => isActive(j.status));
  useEffect(() => {
    if (!polling) return;
    const timer = setTimeout(load, POLL_MS);
    return () => clearTimeout(timer);
  }, [polling, jobs, load]);

  const act = async (job: BulkJob, action: "cancel" | "resume") => {
    setBusyId(job.id);
    setError(null);
    try {
      await (action === "cancel" ? bulkJobsAdminApi.cancel(job.id) : bulkJobsAdminApi.resume(job.id));
      load();
    } catch (e) {
      setError(errorText(e, t("adm.bulk.err_action")));
    } finally {
      setBusyId(null);
    }
  };

  if (jobs.length === 0 && !error) return null;

  return (
    <section className="space-y-2 rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
      <p className="text-xs font-medium text-fg-muted">{t("adm.bulk.jobs_title")}</p>
      {error && <p className="text-xs text-danger">{error}</p>}
      {jobs.map((job) => {
        const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
        const missed = job.failed + job.skipped + job.unknown + job.verify_flagged;
        return (
          <div key={job.id} className="space-y-1.5 rounded-lg border border-[var(--border)] bg-bg px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-fg">
                {t("adm.bulk.job_head", { id: job.id, kind: jobKindLabel(job.kind) })}
                {job.created_by ? ` · ${job.created_by}` : ""}
                {job.created_at ? ` · ${formatDate(job.created_at)}` : ""}
              </span>
              <span className={`font-medium ${job.status === "ERROR" ? "text-danger" : isActive(job.status) ? "text-accent" : job.status === "PAUSED" ? "text-warning" : "text-fg-muted"}`}>
                {jobStatusLabel(job.status)}
              </span>
            </div>
            {job.kind === "days" && job.params.days != null && (
              <p className="text-xs text-fg-muted">{t("adm.bulk.job_plus_days", { days: daysLabel(t, lang, job.params.days) })}</p>
            )}
            {job.kind === "message" && job.params.text_preview && (
              <p className="truncate text-xs text-fg-muted">{t("adm.bulk.text_quote", { text: job.params.text_preview })}</p>
            )}
            <div className="h-1.5 overflow-hidden rounded-full bg-bg-subtle">
              <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
            </div>
            <p className="text-xs text-fg-muted">
              {t("adm.bulk.progress", { done: job.done, total: job.total, counters: jobCounters(job) })}
            </p>
            {job.pause_reason && <p className="text-xs text-warning">{job.pause_reason}</p>}
            <div className="flex flex-wrap gap-2">
              {canManage && ["QUEUED", "PROCESSING", "PAUSED", "ERROR"].includes(job.status) && (
                <button className={BUTTON} disabled={busyId === job.id} onClick={() => act(job, "cancel")}>
                  {t("adm.bulk.stop")}
                </button>
              )}
              {canManage && (job.status === "PAUSED" || job.status === "ERROR") && (
                <button className={BUTTON} disabled={busyId === job.id} onClick={() => act(job, "resume")}>
                  {t("adm.bulk.resume")}
                </button>
              )}
              {missed > 0 && (
                <button className={BUTTON} onClick={() => setOpenId(openId === job.id ? null : job.id)}>
                  {t("adm.bulk.who_missed")}
                </button>
              )}
              {canManage && job.kind === "days" && job.applied > 0 && (job.status === "COMPLETED" || job.status === "CANCELED") && (
                <button className={BUTTON} onClick={() => onWriteRecipients(job.id)}>
                  {t("adm.bulk.write_recipients")}
                </button>
              )}
            </div>
            {openId === job.id && <JobItems job={job} />}
          </div>
        );
      })}
    </section>
  );
}
