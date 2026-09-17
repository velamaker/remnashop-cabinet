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
import { ruDays } from "@/lib/pluralRu";
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

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback);
const isUnsupported = (e: unknown) => e instanceof ApiError && e.status === 501;

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
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
          <button onClick={onClose} aria-label="Закрыть" className="-m-1 rounded-lg p-2 text-fg-muted hover:text-fg">
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
  const toggle = (c: BulkChannel, on: boolean) =>
    onChange(ALL_CHANNELS.filter((x) => (x === c ? on : value.includes(x))));
  return (
    <div className="space-y-1.5">
      <Check checked={value.includes("telegram")} onChange={(v) => toggle("telegram", v)}>
        Telegram
      </Check>
      <Check checked={value.includes("cabinet")} onChange={(v) => toggle("cabinet", v)}>
        В ленту уведомлений кабинета (и push, если Telegram не доставил)
      </Check>
      <Check checked={value.includes("email")} onChange={(v) => toggle("email", v)}>
        Письмом — тем, до кого не дошли Telegram и push (только подтверждённая почта)
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
  const [filters, filtersKey] = useStableFilters(rawFilters);
  const [days, setDays] = useState("3");
  const [includeTrial, setIncludeTrial] = useState(false);
  const [includeLimited, setIncludeLimited] = useState(false);
  const [notify, setNotify] = useState(false);
  const [text, setText] = useState(() => compensationText(3));
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
    if (!textTouched && daysValid) setText(compensationText(daysNum));
  }, [daysNum, daysValid, textTouched]);

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
          else setError(errorText(e, "Не удалось посчитать, кого затронет"));
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
        setError("Выборка изменилась, пока вы смотрели предпросмотр. Проверьте цифры ещё раз.");
        setStep("form");
        setReload((n) => n + 1);
      } else {
        setError(errorText(e, "Не удалось запустить — попробуйте ещё раз"));
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  if (step === "confirm" && preview) {
    return (
      <Modal title="Добавить дни подписки" onClose={onClose}>
        <p className="font-medium">
          Добавить {ruDays(daysNum)} {eligible} {eligible === 1 ? "подписке" : "подпискам"}?
        </p>
        <p className="text-fg-muted">
          Срок в панели VPN обновится у каждого по очереди, примерно за {estimateMinutes("days", eligible)} мин. Задачу
          можно остановить — уже добавленные дни останутся.
        </p>
        {needsRepeat && repeat && (
          <div className="space-y-2 rounded-lg bg-warning/10 px-3 py-2">
            <p className="text-warning">
              Этим людям уже добавляли дни за последние 24 часа: {repeat.count} (задача №{repeat.job_id}, +{repeat.days} дн.)
            </p>
            <Check checked={allowRepeat} onChange={setAllowRepeat}>
              Понимаю, добавить ещё раз
            </Check>
          </div>
        )}
        {error && <p className="text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} onClick={() => setStep("form")} disabled={busy}>
            Назад
          </button>
          <button className={PRIMARY} onClick={start} disabled={busy || (needsRepeat && !allowRepeat)}>
            {busy ? "Запускаю…" : "Да, добавить"}
          </button>
        </div>
      </Modal>
    );
  }

  const lines = skippedLines(preview?.skipped);
  return (
    <Modal title="Добавить дни подписки" onClose={onClose}>
      <div>
        <label htmlFor="bulk-days" className="mb-1 block text-xs text-fg-muted">
          Сколько дней добавить
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
        <p className={`mt-1 text-xs ${daysValid ? "text-fg-subtle" : "text-danger"}`}>от 1 до 365</p>
      </div>
      <div className="space-y-1.5">
        <Check checked={includeTrial} onChange={setIncludeTrial}>
          Также пробным подпискам
        </Check>
        <Check checked={includeLimited} onChange={setIncludeLimited}>
          Также тем, у кого исчерпан трафик
        </Check>
        {includeLimited && (
          <p className="pl-6 text-xs text-warning">Панель после продления снова пришлёт им сообщение, что трафик исчерпан</p>
        )}
      </div>

      <div className="space-y-1 rounded-lg border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
        <p className="text-xs font-medium text-fg-muted">Кого затронет</p>
        {loading ? (
          <Spinner />
        ) : preview ? (
          <>
            <p>
              Добавим {ruDays(daysNum)}: {preview.apply} {preview.apply === 1 ? "подписке" : "подпискам"}
            </p>
            {preview.apply_frozen > 0 && (
              <p>Из них на паузе: {preview.apply_frozen} — дни добавятся к остатку паузы</p>
            )}
            {preview.deferred > 0 && (
              <p>
                Недавно платили или меняли подписку: {preview.deferred} — обработаем в конце; если изменение ещё идёт,
                пропустим с пометкой
              </p>
            )}
            <p className="text-xs text-fg-muted">
              Перед изменением каждого сверим с панелью VPN: если данные расходятся, человека пропустим и покажем в
              списке «Кто не получил»
            </p>
            {lines.length > 0 && (
              <div className="pt-1">
                <p className="text-xs font-medium text-fg-muted">Не получат:</p>
                <ul className="list-disc pl-5 text-xs text-fg-muted">
                  {lines.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </div>
            )}
            {preview.active_job_id != null && (
              <p className="text-xs text-warning">Уже идёт задача №{preview.active_job_id} — новая не запустится, пока она не закончится</p>
            )}
          </>
        ) : null}
      </div>

      <div className="space-y-2">
        <Check checked={notify} onChange={setNotify}>
          Сообщить им об этом
        </Check>
        {notify && (
          <div className="space-y-2 pl-6">
            <textarea
              aria-label="Текст сообщения"
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
          Отмена
        </button>
        <button className={PRIMARY} onClick={() => setStep("confirm")} disabled={!canNext}>
          Далее
        </button>
      </div>
    </Modal>
  );
}

// ─── «Сообщение отфильтрованным» ────────────────────────────────────────────

function testResultText(r: { telegram: boolean; reason: string | null }): string {
  if (r.telegram) return "Отправлено вам в Telegram — так его увидят получатели";
  if (r.reason === "no_telegram") return "У вашего аккаунта нет Telegram — проверить отправку нельзя";
  return "Telegram не доставил сообщение вам — проверьте, не заблокирован ли бот";
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
        else setError(errorText(e, "Не удалось посчитать получателей"));
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
      setTestNote(errorText(e, "Не удалось отправить проверку"));
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
        setError("Выборка изменилась, пока вы смотрели предпросмотр. Проверьте цифры ещё раз.");
        setStep("form");
        setReload((n) => n + 1);
      } else {
        setError(errorText(e, "Не удалось запустить — попробуйте ещё раз"));
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  if (step === "confirm" && preview) {
    return (
      <Modal title="Сообщение отфильтрованным" onClose={onClose}>
        <p className="font-medium">Отправить сообщение {preview.recipients} получателям?</p>
        <p className="text-fg-muted">Отозвать уже ушедшие сообщения будет нельзя.</p>
        {repeatAsked && (
          <div className="space-y-2 rounded-lg bg-warning/10 px-3 py-2">
            <p className="text-warning">{repeatAsked}</p>
            <Check checked={allowRepeat} onChange={setAllowRepeat}>
              Понимаю, отправить ещё раз
            </Check>
          </div>
        )}
        {error && <p className="text-danger">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} onClick={() => setStep("form")} disabled={busy}>
            Назад
          </button>
          <button className={PRIMARY} onClick={start} disabled={busy || (!!repeatAsked && !allowRepeat)}>
            {busy ? "Отправляю…" : "Да, отправить"}
          </button>
        </div>
      </Modal>
    );
  }

  const ch = preview?.by_channel;
  return (
    <Modal title="Сообщение отфильтрованным" onClose={onClose}>
      {sourceJobId != null && <p className="text-xs text-fg-muted">Получатели — те, кому задача №{sourceJobId} добавила дни.</p>}
      <div>
        <textarea
          aria-label="Текст сообщения"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          placeholder="Текст сообщения. Можно разметку Telegram: <b>, <i>, <a href=&quot;…&quot;>"
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
            <p className="font-medium">Получателей: {preview.recipients}</p>
            <p className="text-xs text-fg-muted">в Telegram: {ch.telegram}</p>
            {ch.telegram_bot_blocked > 0 && (
              <p className="text-xs text-fg-muted">
                заблокировали бота: {ch.telegram_bot_blocked} — им сразу запасные каналы
              </p>
            )}
            <p className="text-xs text-fg-muted">
              без Telegram: с push — {ch.push_only}, с подтверждённой почтой — {ch.email_only}, только лента кабинета —{" "}
              {ch.cabinet_only}
            </p>
            {ch.unreachable > 0 && <p className="text-xs text-warning">не достучаться: {ch.unreachable}</p>}
            {(preview.skipped.BLOCKED ?? 0) > 0 && (
              <p className="text-xs text-fg-muted">заблокированы в сервисе и пропущены: {preview.skipped.BLOCKED}</p>
            )}
            {channels.includes("email") && !preview.email_enabled && (
              <p className="text-xs text-warning">Почта выключена в настройках — письма не уйдут</p>
            )}
            {preview.active_job_id != null && (
              <p className="text-xs text-warning">Уже идёт рассылка №{preview.active_job_id} — новая не запустится, пока она не закончится</p>
            )}
          </>
        ) : channels.length === 0 ? (
          <p className="text-xs text-fg-muted">Выберите хотя бы один канал</p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className={BUTTON} onClick={sendTest} disabled={testing || !textValid}>
          {testing ? "Отправляю…" : "Проверить на себе"}
        </button>
        {testNote && <span className="text-xs text-fg-muted">{testNote}</span>}
      </div>

      {error && <p className="text-danger">{error}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button className={BUTTON} onClick={onClose}>
          Отмена
        </button>
        <button className={PRIMARY} onClick={() => setStep("confirm")} disabled={!canNext}>
          Далее
        </button>
      </div>
    </Modal>
  );
}

// ─── «Фоновые задачи» ───────────────────────────────────────────────────────

function JobItems({ job }: { job: BulkJob }) {
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
        if (!cancelled) setError(errorText(e, "Не удалось загрузить список"));
      });
    return () => {
      cancelled = true;
    };
  }, [job.id, job.verify_flagged, job.done]);

  if (error) return <p className="text-xs text-danger">{error}</p>;
  if (!items) return <Spinner />;
  if (items.length === 0) return <p className="text-xs text-fg-muted">Все получили</p>;
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
  const [jobs, setJobs] = useState<BulkJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const unsupported = useRef(onUnsupported);
  unsupported.current = onUnsupported;
  const canManage = fullAccess && !readonly;

  const load = useCallback(() => {
    bulkJobsAdminApi
      .jobs(10)
      .then((r) => {
        setJobs(r.items);
        setError(null);
      })
      .catch((e) => {
        // Панель рисуется сама, без нажатия, и видна всем админам: бэкенд, у которого
        // этой ручки нет вовсе (501 адаптера или 404 бота старее кабинета), — не
        // ошибка на странице, а просто отсутствие журнала.
        if (isUnsupported(e) || (e instanceof ApiError && e.status === 404)) unsupported.current("users.bulk.jobs");
        else setError(errorText(e, "Не удалось загрузить фоновые задачи"));
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
      setError(errorText(e, "Не удалось"));
    } finally {
      setBusyId(null);
    }
  };

  if (jobs.length === 0 && !error) return null;

  return (
    <section className="space-y-2 rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2.5">
      <p className="text-xs font-medium text-fg-muted">Фоновые задачи</p>
      {error && <p className="text-xs text-danger">{error}</p>}
      {jobs.map((job) => {
        const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
        const missed = job.failed + job.skipped + job.unknown + job.verify_flagged;
        return (
          <div key={job.id} className="space-y-1.5 rounded-lg border border-[var(--border)] bg-bg px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-fg">
                №{job.id} · {jobKindLabel(job.kind)}
                {job.created_by ? ` · ${job.created_by}` : ""}
                {job.created_at ? ` · ${formatDate(job.created_at)}` : ""}
              </span>
              <span className={`font-medium ${job.status === "ERROR" ? "text-danger" : isActive(job.status) ? "text-accent" : job.status === "PAUSED" ? "text-warning" : "text-fg-muted"}`}>
                {jobStatusLabel(job.status)}
              </span>
            </div>
            {job.kind === "days" && job.params.days != null && (
              <p className="text-xs text-fg-muted">+{ruDays(job.params.days)}</p>
            )}
            {job.kind === "message" && job.params.text_preview && (
              <p className="truncate text-xs text-fg-muted">«{job.params.text_preview}»</p>
            )}
            <div className="h-1.5 overflow-hidden rounded-full bg-bg-subtle">
              <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
            </div>
            <p className="text-xs text-fg-muted">
              {job.done} из {job.total} · {jobCounters(job)}
            </p>
            {job.pause_reason && <p className="text-xs text-warning">{job.pause_reason}</p>}
            <div className="flex flex-wrap gap-2">
              {canManage && ["QUEUED", "PROCESSING", "PAUSED", "ERROR"].includes(job.status) && (
                <button className={BUTTON} disabled={busyId === job.id} onClick={() => act(job, "cancel")}>
                  Остановить
                </button>
              )}
              {canManage && (job.status === "PAUSED" || job.status === "ERROR") && (
                <button className={BUTTON} disabled={busyId === job.id} onClick={() => act(job, "resume")}>
                  Продолжить
                </button>
              )}
              {missed > 0 && (
                <button className={BUTTON} onClick={() => setOpenId(openId === job.id ? null : job.id)}>
                  Кто не получил
                </button>
              )}
              {canManage && job.kind === "days" && job.applied > 0 && (job.status === "COMPLETED" || job.status === "CANCELED") && (
                <button className={BUTTON} onClick={() => onWriteRecipients(job.id)}>
                  Написать получившим
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
