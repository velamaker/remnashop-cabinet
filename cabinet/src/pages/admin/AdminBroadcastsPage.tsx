import { useEffect, useState, useCallback } from "react";
import { RefreshCw, AlertCircle, CheckCircle, XCircle, Clock, Send, Eye, EyeOff } from "lucide-react";
import { broadcastsAdminApi, plansAdminApi, type AdminBroadcast, type AdminPlan, type BroadcastChannel } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { pluralFor } from "@/lib/pluralRu";
import { useT } from "@/i18n/I18nContext";
import { getActiveLang, translate } from "@/i18n/translate";

// Предпросмотр «как в Telegram»: экранируем всё, затем возвращаем только
// разрешённый Telegram whitelist тегов (b/i/u/s/code/pre/a). Скрипты/атрибуты
// не проходят — dangerouslySetInnerHTML безопасен.
// Разрешаем ссылки ТОЛЬКО с безопасными схемами. javascript:/data:/vbscript: и пр.
// в href — вектор XSS (клик исполняет скрипт в origin кабинета), поэтому режем их.
function safeHref(url: string): string | null {
  const u = url.trim();
  return /^(https?:\/\/|tg:\/\/|mailto:)/i.test(u) ? u : null;
}

function toPreviewHtml(raw: string): string {
  let s = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  s = s.replace(/&lt;(\/?)(b|strong|i|em|u|s|code|pre)&gt;/gi, "<$1$2>");
  s = s.replace(/&lt;a href="([^"]*)"&gt;/gi, (_m, href: string) => {
    const safe = safeHref(href);
    // Небезопасная/пустая ссылка → показываем как текст (тег не восстанавливаем).
    return safe
      ? `<a href="${safe}" target="_blank" rel="noreferrer" class="text-accent underline">`
      : "";
  });
  s = s.replace(/&lt;\/a&gt;/gi, "</a>");
  return s;
}

// «7 дней» / «3 дня» / «1 день» — и «1 day» / «7 days» по-английски: форму выбирает
// pluralFor ПО ЯЗЫКУ, сам текст — из словаря. Через pluralRu на английском выходило
// бы «21 day», потому что русское правило смотрит на последнюю цифру.
function daysLabel(n: number): string {
  return translate(
    pluralFor(
      getActiveLang(),
      n,
      "adm.broadcasts.days_one",
      "adm.broadcasts.days_few",
      "adm.broadcasts.days_many",
    ),
    { n },
  );
}

const AUDIENCE_KEYS: Record<string, string> = {
  // TG-история хранит аудиторию enum'ом базы (ALL/SUBSCRIBED/…).
  ALL: "adm.broadcasts.aud_all",
  SUBSCRIBED: "adm.broadcasts.aud_subscribed",
  UNSUBSCRIBED: "adm.broadcasts.aud_unsubscribed",
  TRIAL: "adm.broadcasts.aud_trial",
  EXPIRED: "adm.broadcasts.aud_expired",
  PLAN: "adm.broadcasts.aud_plan",
  // Бэкенд узнаёт «Истекают скоро» по payload и отдаёт отдельным ключом.
  TG_EXPIRING: "adm.broadcasts.aud_tg_expiring",
  // Email-история хранит сегмент (EMAIL_*).
  EMAIL_ALL: "adm.broadcasts.aud_email_all",
  EMAIL_SUBSCRIBED: "adm.broadcasts.aud_email_subscribed",
  EMAIL_TRIAL: "adm.broadcasts.aud_email_trial",
  EMAIL_EXPIRING: "adm.broadcasts.aud_email_expiring",
  EMAIL_EXPIRED: "adm.broadcasts.aud_email_expired",
  EMAIL: "adm.broadcasts.aud_email", // legacy-записи без сегмента
};

const STATUS_CONFIG: Record<string, { labelKey: string; icon: React.ElementType; cls: string }> = {
  PROCESSING: { labelKey: "adm.broadcasts.status_processing", icon: Clock, cls: "text-warning" },
  COMPLETED: { labelKey: "adm.broadcasts.status_completed", icon: CheckCircle, cls: "text-success" },
  CANCELED: { labelKey: "adm.broadcasts.status_canceled", icon: XCircle, cls: "text-danger" },
  ERROR: { labelKey: "adm.broadcasts.status_error", icon: XCircle, cls: "text-danger" },
};

type ChannelItem = { key: BroadcastChannel; labelKey: string; hintKey: string };
// id — для логики (какие подблоки показывать), titleKey — то, что видит человек.
const CHANNEL_GROUPS: { id: "TG" | "EMAIL"; titleKey: string; items: ChannelItem[] }[] = [
  {
    id: "TG",
    titleKey: "adm.broadcasts.grp_tg",
    items: [
      { key: "TG_ALL", labelKey: "adm.broadcasts.ch_all", hintKey: "adm.broadcasts.hint_tg_all" },
      { key: "TG_PLAN", labelKey: "adm.broadcasts.ch_plan", hintKey: "adm.broadcasts.hint_tg_plan" },
      { key: "TG_SUBSCRIBED", labelKey: "adm.broadcasts.ch_subscribed", hintKey: "adm.broadcasts.hint_tg_subscribed" },
      { key: "TG_UNSUBSCRIBED", labelKey: "adm.broadcasts.ch_unsubscribed", hintKey: "adm.broadcasts.hint_tg_unsubscribed" },
      { key: "TG_TRIAL", labelKey: "adm.broadcasts.ch_trial", hintKey: "adm.broadcasts.hint_trial" },
      { key: "TG_EXPIRING", labelKey: "adm.broadcasts.ch_expiring", hintKey: "adm.broadcasts.hint_tg_expiring" },
      { key: "TG_EXPIRED", labelKey: "adm.broadcasts.ch_expired", hintKey: "adm.broadcasts.hint_expired" },
    ],
  },
  {
    id: "EMAIL",
    titleKey: "adm.broadcasts.grp_email",
    items: [
      { key: "EMAIL_ALL", labelKey: "adm.broadcasts.ch_all", hintKey: "adm.broadcasts.hint_email_all" },
      { key: "EMAIL_SUBSCRIBED", labelKey: "adm.broadcasts.ch_subscribed", hintKey: "adm.broadcasts.hint_email_subscribed" },
      { key: "EMAIL_TRIAL", labelKey: "adm.broadcasts.ch_trial", hintKey: "adm.broadcasts.hint_trial" },
      { key: "EMAIL_EXPIRING", labelKey: "adm.broadcasts.ch_email_expiring", hintKey: "adm.broadcasts.hint_email_expiring" },
      { key: "EMAIL_EXPIRED", labelKey: "adm.broadcasts.ch_expired", hintKey: "adm.broadcasts.hint_expired" },
    ],
  },
];

const channelOf = (k: BroadcastChannel) => (k.startsWith("EMAIL") ? "EMAIL" : "TG");
const isAll = (k: BroadcastChannel) => k === "TG_ALL" || k === "EMAIL_ALL";

// «Истекают скоро» — подмножество «С подпиской» и может совпасть с любым тарифом:
// вместе с ними часть людей получила бы сообщение дважды.
const EXPIRING_OVERLAPS: BroadcastChannel[] = ["TG_ALL", "TG_SUBSCRIBED", "TG_PLAN"];
const EXPIRING_DAY_OPTIONS = [3, 7, 14, 30];
const EXPIRING_DEFAULT_DAYS = 7;

/** Подпись рассылки в истории; у «Истекают скоро» — с числом дней. */
function audienceLabel(b: AdminBroadcast): string {
  if (b.audience === "TG_EXPIRING" && b.expiring_days) {
    return translate("adm.broadcasts.aud_tg_expiring_days", { days: daysLabel(b.expiring_days) });
  }
  const key = AUDIENCE_KEYS[b.audience];
  return key ? translate(key) : b.audience;
}

function CreateBroadcast({ onCreated }: { onCreated: () => void }) {
  const t = useT();
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Set<BroadcastChannel>>(new Set());
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [sending, setSending] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [planId, setPlanId] = useState<number | "">("");
  const [expiringDays, setExpiringDays] = useState(EXPIRING_DEFAULT_DAYS);

  const planSelected = selected.has("TG_PLAN");
  const expiringSelected = selected.has("TG_EXPIRING");

  // Счётчики «по тарифу» и «истекают скоро» зависят от выбора, поэтому
  // перезапрашиваем при его смене. Остальные аудитории от него не зависят и не мигают.
  useEffect(() => {
    broadcastsAdminApi
      .audienceCounts(typeof planId === "number" ? planId : undefined, expiringDays)
      .then(setCounts)
      .catch(() => {});
  }, [planId, expiringDays]);

  // Тарифы тянем только когда они понадобились — на обычную рассылку лишний запрос ни к чему.
  useEffect(() => {
    if (!planSelected || plans.length > 0) return;
    plansAdminApi
      .list()
      .then((r) => setPlans(r.items ?? []))
      .catch(() => {});
  }, [planSelected, plans.length]);

  // Внутри одного канала «Все» и сегменты по статусу взаимоисключимы: «Все» —
  // надмножество сегментов, иначе часть юзеров получит рассылку дважды.
  // Telegram и Email независимы друг от друга.
  const conflicts = (k: BroadcastChannel, s: Set<BroadcastChannel>): boolean => {
    if (k === "TG_EXPIRING" && EXPIRING_OVERLAPS.some((x) => s.has(x))) return true;
    if (EXPIRING_OVERLAPS.includes(k) && s.has("TG_EXPIRING")) return true;
    const ch = channelOf(k);
    const same = [...s].filter((x) => channelOf(x) === ch);
    if (isAll(k)) return same.some((x) => !isAll(x));
    return same.some((x) => isAll(x));
  };
  // Почему пункт заблокирован — для подсказки при наведении.
  const conflictReason = (k: BroadcastChannel): string =>
    (k === "TG_EXPIRING" && EXPIRING_OVERLAPS.some((x) => selected.has(x))) ||
    (EXPIRING_OVERLAPS.includes(k) && selected.has("TG_EXPIRING"))
      ? t("adm.broadcasts.conflict_expiring")
      : t("adm.broadcasts.conflict_all");

  const toggle = (k: BroadcastChannel) => {
    if (!selected.has(k) && conflicts(k, selected)) return; // заблокирован — игнор
    setConfirm(false);
    setMsg(null);
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  };

  const recipients = [...selected].reduce((s, k) => s + (counts?.[k] ?? 0), 0);

  // Кабинет может стоять на отдельном сервере и быть новее бота. Сегмент, о
  // котором бот не знает, не показываем: счётчики — единственный ответ, где
  // видно, какие каналы он умеет. Ждём загрузки счётчиков, чтобы пункт не мигал.
  // «Истекают скоро» — только когда ключ уже пришёл: этот сегмент бывает не у всех
  // ботов (и не встаёт, если не применилась правка рассылок), а мелькнувший и
  // пропавший пункт хуже, чем появившийся чуть позже.
  const supported = (c: ChannelItem) => {
    if (c.key === "TG_EXPIRING") return counts !== null && "TG_EXPIRING" in counts;
    return c.key !== "TG_PLAN" || counts === null || "TG_PLAN" in counts;
  };

  const submit = async () => {
    setErr(null);
    setMsg(null);
    if (!text.trim()) return setErr(t("adm.broadcasts.err_text"));
    if (selected.size === 0) return setErr(t("adm.broadcasts.err_channel"));
    if (planSelected && typeof planId !== "number") return setErr(t("adm.broadcasts.err_plan"));
    if (!confirm) return setConfirm(true);
    setSending(true);
    try {
      await broadcastsAdminApi.create(
        text.trim(),
        [...selected],
        typeof planId === "number" ? planId : undefined,
        expiringSelected ? expiringDays : undefined,
      );
      setMsg(t("adm.broadcasts.started"));
      setText("");
      setSelected(new Set());
      setPlanId("");
      setExpiringDays(EXPIRING_DEFAULT_DAYS);
      setConfirm(false);
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : t("adm.broadcasts.err_create"));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-fg">{t("adm.broadcasts.new_title")}</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          {t("adm.broadcasts.new_hint")}
        </p>
      </div>

      <div>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setConfirm(false); }}
          rows={5}
          maxLength={4000}
          placeholder={t("adm.broadcasts.text_ph")}
          className="w-full resize-none rounded-xl border border-[var(--border)] bg-bg-raised px-3 py-2.5 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
        />
        <div className="mt-1.5 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setPreview((v) => !v)}
            disabled={!text.trim()}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg disabled:opacity-40"
          >
            {preview ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            {preview ? t("adm.broadcasts.preview_hide") : t("adm.broadcasts.preview_show")}
          </button>
          <span className="text-xs text-fg-subtle">{text.length}/4000</span>
        </div>
      </div>

      {preview && text.trim() && (
        <div className="rounded-xl border border-border-subtle bg-bg-raised p-4">
          <p className="mb-2 text-xs text-fg-subtle">{t("adm.broadcasts.preview_note")}</p>
          <div className="max-w-md rounded-2xl rounded-tl-sm bg-accent-subtle px-4 py-2.5">
            <p
              className="whitespace-pre-wrap break-words text-sm text-fg"
              dangerouslySetInnerHTML={{ __html: toPreviewHtml(text) }}
            />
          </div>
        </div>
      )}

      <div className="space-y-4">
        {CHANNEL_GROUPS.map((group) => (
          <div key={group.id}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">{t(group.titleKey)}</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {group.items.filter(supported).map((c) => {
                const on = selected.has(c.key);
                const blocked = !on && conflicts(c.key, selected);
                const cnt = counts?.[c.key];
                return (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => toggle(c.key)}
                    disabled={blocked}
                    title={blocked ? conflictReason(c.key) : undefined}
                    className={`flex items-start gap-3 rounded-xl border p-3 text-left transition-colors ${
                      on
                        ? "border-accent bg-accent-subtle"
                        : blocked
                          ? "cursor-not-allowed border-border-subtle bg-bg-raised opacity-40"
                          : "border-border-subtle bg-bg-raised hover:border-[var(--border)]"
                    }`}
                  >
                    <span className={`mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md border ${on ? "border-accent bg-accent text-accent-fg" : "border-[var(--border)]"}`}>
                      {on && <CheckCircle className="h-3.5 w-3.5" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-fg">{t(c.labelKey)}</span>
                        <span className="flex-shrink-0 text-xs text-fg-muted">{cnt ?? "…"}</span>
                      </span>
                      <span className="block text-xs text-fg-subtle">{t(c.hintKey)}</span>
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Выбор тарифа появляется только под группой Telegram и только когда
                выбран канал «По тарифу» — иначе он был бы мёртвым полем на экране. */}
            {group.id === "TG" && expiringSelected && (
              <div className="mt-2 rounded-xl border border-accent/40 bg-accent-subtle/40 p-3">
                <label htmlFor="broadcast-expiring-days" className="mb-1.5 block text-xs font-medium text-fg-muted">
                  {t("adm.broadcasts.expiring_days_label")}
                </label>
                <select
                  id="broadcast-expiring-days"
                  value={expiringDays}
                  onChange={(e) => {
                    setExpiringDays(Number(e.target.value));
                    setConfirm(false);
                    setErr(null);
                  }}
                  className="w-full rounded-xl border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  {EXPIRING_DAY_OPTIONS.map((d) => (
                    <option key={d} value={d}>
                      {daysLabel(d)}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-fg-subtle">
                  {t("adm.broadcasts.expiring_hint", {
                    days: daysLabel(expiringDays),
                    n: counts?.TG_EXPIRING ?? "…",
                  })}
                </p>
              </div>
            )}

            {group.id === "TG" && planSelected && (
              <div className="mt-2 rounded-xl border border-accent/40 bg-accent-subtle/40 p-3">
                <label className="mb-1.5 block text-xs font-medium text-fg-muted">
                  {t("adm.broadcasts.plan_label")}
                </label>
                <select
                  value={planId}
                  onChange={(e) => {
                    setPlanId(e.target.value ? Number(e.target.value) : "");
                    setConfirm(false);
                    setErr(null);
                  }}
                  className="w-full rounded-xl border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
                >
                  <option value="">{t("adm.broadcasts.plan_ph")}</option>
                  {plans.map((pl) => (
                    <option key={pl.id} value={pl.id}>
                      {pl.name}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-fg-subtle">
                  {typeof planId === "number"
                    ? t("adm.broadcasts.plan_hint", { n: counts?.TG_PLAN ?? "…" })
                    : t("adm.broadcasts.plan_hint_empty")}
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-2.5 text-sm text-danger"><AlertCircle className="h-4 w-4" />{err}</div>}
      {msg && <div className="flex items-center gap-2 rounded-xl bg-success/10 px-4 py-2.5 text-sm text-success"><CheckCircle className="h-4 w-4" />{msg}</div>}

      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-fg-muted">
          {selected.size > 0
            ? t("adm.broadcasts.recipients", { n: recipients })
            : t("adm.broadcasts.no_channels")}
        </span>
        <button
          onClick={submit}
          disabled={sending}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
            confirm ? "bg-danger text-white hover:opacity-90" : "btn-gradient border-0 text-white"
          }`}
        >
          <Send className="h-4 w-4" />
          {sending
            ? t("adm.broadcasts.sending")
            : confirm
              ? t("adm.broadcasts.confirm_send", { n: recipients })
              : t("adm.broadcasts.send")}
        </button>
      </div>
    </div>
  );
}

function BroadcastCard({ b, onRefresh }: { b: AdminBroadcast; onRefresh: (id: string) => void }) {
  const t = useT();
  const cfg = STATUS_CONFIG[b.status];
  const Icon = cfg?.icon ?? Clock;
  const label = cfg ? t(cfg.labelKey) : b.status;
  const cls = cfg?.cls ?? "text-fg-muted";
  const successRate = b.total_count > 0 ? Math.round(b.success_count / b.total_count * 100) : 0;

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Icon className={`h-4 w-4 ${cls}`} />
            <span className={`text-sm font-medium ${cls}`}>{label}</span>
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            {audienceLabel(b)}
            {b.created_at && ` · ${formatDate(b.created_at)}`}
          </p>
        </div>
        {b.status === "PROCESSING" && (
          <button onClick={() => onRefresh(b.task_id)} className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors" title={t("adm.broadcasts.refresh")}>
            <RefreshCw className="h-4 w-4" />
          </button>
        )}
      </div>

      {b.total_count > 0 && (
        <>
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-fg-muted">{t("adm.broadcasts.progress")}</span>
            <span className="font-medium text-fg">{b.success_count + b.failed_count} / {b.total_count}</span>
          </div>
          <div className="mb-4 h-2 w-full overflow-hidden rounded-full bg-bg-raised">
            <div
              className="h-full rounded-full bg-success transition-all"
              style={{ width: `${successRate}%` }}
            />
          </div>
          <div className="grid grid-cols-3 gap-3 text-xs text-center">
            <div className="rounded-xl bg-bg-raised p-3">
              <p className="text-2xl font-bold text-fg">{b.total_count}</p>
              <p className="text-fg-muted mt-0.5">{t("adm.broadcasts.stat_total")}</p>
            </div>
            <div className="rounded-xl bg-success/10 p-3">
              <p className="text-2xl font-bold text-success">{b.success_count}</p>
              <p className="text-fg-muted mt-0.5">{t("adm.broadcasts.stat_delivered")}</p>
            </div>
            <div className="rounded-xl bg-danger/10 p-3">
              <p className="text-2xl font-bold text-danger">{b.failed_count}</p>
              <p className="text-fg-muted mt-0.5">{t("adm.broadcasts.stat_failed")}</p>
            </div>
          </div>
        </>
      )}

      <p className="mt-3 font-mono text-[10px] text-fg-subtle break-all">{b.task_id}</p>
    </div>
  );
}

export default function AdminBroadcastsPage() {
  const t = useT();
  const [broadcasts, setBroadcasts] = useState<AdminBroadcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    broadcastsAdminApi.list()
      .then(r => setBroadcasts(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : translate("adm.broadcasts.err_generic")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const refreshOne = async (task_id: string) => {
    try {
      const updated = await broadcastsAdminApi.get(task_id);
      setBroadcasts(prev => prev.map(b => b.task_id === task_id ? updated : b));
    } catch {
      /* обновление статуса не критично */
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">{t("adm.broadcasts.title")}</h1>
        <button onClick={load} className="flex items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm text-fg-muted hover:text-fg transition-colors">
          <RefreshCw className="h-4 w-4" /> {t("adm.broadcasts.refresh")}
        </button>
      </div>

      <CreateBroadcast onCreated={load} />

      <h2 className="text-sm font-semibold text-fg-muted">{t("adm.broadcasts.history")}</h2>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : broadcasts.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.broadcasts.empty")}</div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {broadcasts.map(b => <BroadcastCard key={b.task_id} b={b} onRefresh={refreshOne} />)}
        </div>
      )}
    </div>
  );
}
