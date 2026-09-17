import { useEffect, useState, useCallback } from "react";
import { RefreshCw, AlertCircle, CheckCircle, XCircle, Clock, Send, Eye, EyeOff } from "lucide-react";
import { broadcastsAdminApi, plansAdminApi, type AdminBroadcast, type AdminPlan, type BroadcastChannel } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { ruDays } from "@/lib/pluralRu";

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

const AUDIENCE_LABELS: Record<string, string> = {
  // TG-история хранит аудиторию enum'ом базы (ALL/SUBSCRIBED/…).
  ALL: "Telegram · все",
  SUBSCRIBED: "Telegram · с подпиской",
  UNSUBSCRIBED: "Telegram · без подписки",
  TRIAL: "Telegram · пробный период",
  EXPIRED: "Telegram · подписка истекла",
  PLAN: "Telegram · по тарифу",
  // Бэкенд узнаёт «Истекают скоро» по payload и отдаёт отдельным ключом.
  TG_EXPIRING: "Telegram · истекают скоро",
  // Email-история хранит сегмент (EMAIL_*).
  EMAIL_ALL: "Email · все",
  EMAIL_SUBSCRIBED: "Email · с подпиской",
  EMAIL_TRIAL: "Email · пробный период",
  EMAIL_EXPIRING: "Email · заканчивается",
  EMAIL_EXPIRED: "Email · подписка истекла",
  EMAIL: "Email · без Telegram", // legacy-записи без сегмента
};

const STATUS_CONFIG: Record<string, { label: string; icon: React.ElementType; cls: string }> = {
  PROCESSING: { label: "В процессе", icon: Clock, cls: "text-warning" },
  COMPLETED: { label: "Завершена", icon: CheckCircle, cls: "text-success" },
  CANCELED: { label: "Отменена", icon: XCircle, cls: "text-danger" },
  ERROR: { label: "Ошибка", icon: XCircle, cls: "text-danger" },
};

type ChannelItem = { key: BroadcastChannel; label: string; hint: string };
const CHANNEL_GROUPS: { title: string; items: ChannelItem[] }[] = [
  {
    title: "Telegram",
    items: [
      { key: "TG_ALL", label: "Все", hint: "все зарегистрированные в боте" },
      { key: "TG_PLAN", label: "По тарифу", hint: "активные на выбранном тарифе" },
      { key: "TG_SUBSCRIBED", label: "С подпиской", hint: "активная (вкл. пробные)" },
      { key: "TG_UNSUBSCRIBED", label: "Без подписки", hint: "нет активной подписки" },
      { key: "TG_TRIAL", label: "Пробный период", hint: "сейчас на триале" },
      { key: "TG_EXPIRING", label: "Истекают скоро", hint: "активная, без пробных, заканчивается в ближайшие N дней" },
      { key: "TG_EXPIRED", label: "Подписка истекла", hint: "закончилась" },
    ],
  },
  {
    title: "Email · только у кого нет Telegram",
    items: [
      { key: "EMAIL_ALL", label: "Все", hint: "все email-без-Telegram" },
      { key: "EMAIL_SUBSCRIBED", label: "С подпиской", hint: "активная, без пробных" },
      { key: "EMAIL_TRIAL", label: "Пробный период", hint: "сейчас на триале" },
      { key: "EMAIL_EXPIRING", label: "Заканчивается", hint: "истекает в ≤ 7 дней" },
      { key: "EMAIL_EXPIRED", label: "Подписка истекла", hint: "закончилась" },
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
    return `${AUDIENCE_LABELS.TG_EXPIRING} (${ruDays(b.expiring_days)})`;
  }
  return AUDIENCE_LABELS[b.audience] ?? b.audience;
}

function CreateBroadcast({ onCreated }: { onCreated: () => void }) {
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
      ? "Пересекается с «Истекают скоро» (задвоение получателей)"
      : "Нельзя вместе с «Все» этого канала (задвоение получателей)";

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
    if (!text.trim()) return setErr("Введите текст сообщения");
    if (selected.size === 0) return setErr("Выберите хотя бы один канал");
    if (planSelected && typeof planId !== "number") return setErr("Выберите тариф для рассылки по тарифу");
    if (!confirm) return setConfirm(true);
    setSending(true);
    try {
      await broadcastsAdminApi.create(
        text.trim(),
        [...selected],
        typeof planId === "number" ? planId : undefined,
        expiringSelected ? expiringDays : undefined,
      );
      setMsg("Рассылка запущена — прогресс появится в истории ниже");
      setText("");
      setSelected(new Set());
      setPlanId("");
      setExpiringDays(EXPIRING_DEFAULT_DAYS);
      setConfirm(false);
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Не удалось запустить рассылку");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-fg">Новая рассылка</h2>
        <p className="mt-0.5 text-xs text-fg-muted">
          Текст уходит выбранным группам. В Telegram поддерживается HTML-разметка; в email — обычным текстом.
        </p>
      </div>

      <div>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setConfirm(false); }}
          rows={5}
          maxLength={4000}
          placeholder="Текст сообщения…"
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
            {preview ? "Скрыть предпросмотр" : "Предпросмотр"}
          </button>
          <span className="text-xs text-fg-subtle">{text.length}/4000</span>
        </div>
      </div>

      {preview && text.trim() && (
        <div className="rounded-xl border border-border-subtle bg-bg-raised p-4">
          <p className="mb-2 text-xs text-fg-subtle">Так увидят в Telegram (в email — обычным текстом, без разметки):</p>
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
          <div key={group.title}>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-fg-subtle">{group.title}</p>
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
                        <span className="text-sm font-medium text-fg">{c.label}</span>
                        <span className="flex-shrink-0 text-xs text-fg-muted">{cnt ?? "…"}</span>
                      </span>
                      <span className="block text-xs text-fg-subtle">{c.hint}</span>
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Выбор тарифа появляется только под группой Telegram и только когда
                выбран канал «По тарифу» — иначе он был бы мёртвым полем на экране. */}
            {group.title === "Telegram" && expiringSelected && (
              <div className="mt-2 rounded-xl border border-accent/40 bg-accent-subtle/40 p-3">
                <label htmlFor="broadcast-expiring-days" className="mb-1.5 block text-xs font-medium text-fg-muted">
                  Истекают в ближайшие
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
                      {ruDays(d)}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-fg-subtle">
                  {`Получат те, у кого подписка заканчивается в ближайшие ${ruDays(expiringDays)}: ${counts?.TG_EXPIRING ?? "…"}. `}
                  Это не то же, что фильтр «Истечение» в списке пользователей: здесь без пробных и резерва.
                </p>
              </div>
            )}

            {group.title === "Telegram" && planSelected && (
              <div className="mt-2 rounded-xl border border-accent/40 bg-accent-subtle/40 p-3">
                <label className="mb-1.5 block text-xs font-medium text-fg-muted">
                  Тариф для рассылки *
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
                  <option value="">— выберите тариф —</option>
                  {plans.map((pl) => (
                    <option key={pl.id} value={pl.id}>
                      {pl.name}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-fg-subtle">
                  {typeof planId === "number"
                    ? `Получат только те, у кого сейчас активна эта подписка: ${counts?.TG_PLAN ?? "…"}`
                    : "Пока тариф не выбран, рассылка не отправится"}
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
          {selected.size > 0 ? `Получателей: ~${recipients}` : "Каналы не выбраны"}
        </span>
        <button
          onClick={submit}
          disabled={sending}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
            confirm ? "bg-danger text-white hover:opacity-90" : "btn-gradient border-0 text-white"
          }`}
        >
          <Send className="h-4 w-4" />
          {sending ? "Запуск…" : confirm ? `Точно отправить ~${recipients}?` : "Отправить"}
        </button>
      </div>
    </div>
  );
}

function BroadcastCard({ b, onRefresh }: { b: AdminBroadcast; onRefresh: (id: string) => void }) {
  const cfg = STATUS_CONFIG[b.status] ?? { label: b.status, icon: Clock, cls: "text-fg-muted" };
  const Icon = cfg.icon;
  const successRate = b.total_count > 0 ? Math.round(b.success_count / b.total_count * 100) : 0;

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Icon className={`h-4 w-4 ${cfg.cls}`} />
            <span className={`text-sm font-medium ${cfg.cls}`}>{cfg.label}</span>
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            {audienceLabel(b)}
            {b.created_at && ` · ${formatDate(b.created_at)}`}
          </p>
        </div>
        {b.status === "PROCESSING" && (
          <button onClick={() => onRefresh(b.task_id)} className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors" title="Обновить">
            <RefreshCw className="h-4 w-4" />
          </button>
        )}
      </div>

      {b.total_count > 0 && (
        <>
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-fg-muted">Прогресс</span>
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
              <p className="text-fg-muted mt-0.5">Всего</p>
            </div>
            <div className="rounded-xl bg-success/10 p-3">
              <p className="text-2xl font-bold text-success">{b.success_count}</p>
              <p className="text-fg-muted mt-0.5">Доставлено</p>
            </div>
            <div className="rounded-xl bg-danger/10 p-3">
              <p className="text-2xl font-bold text-danger">{b.failed_count}</p>
              <p className="text-fg-muted mt-0.5">Ошибок</p>
            </div>
          </div>
        </>
      )}

      <p className="mt-3 font-mono text-[10px] text-fg-subtle break-all">{b.task_id}</p>
    </div>
  );
}

export default function AdminBroadcastsPage() {
  const [broadcasts, setBroadcasts] = useState<AdminBroadcast[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    broadcastsAdminApi.list()
      .then(r => setBroadcasts(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : "Ошибка"))
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
        <h1 className="text-2xl font-bold text-fg">Рассылки</h1>
        <button onClick={load} className="flex items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm text-fg-muted hover:text-fg transition-colors">
          <RefreshCw className="h-4 w-4" /> Обновить
        </button>
      </div>

      <CreateBroadcast onCreated={load} />

      <h2 className="text-sm font-semibold text-fg-muted">История</h2>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : broadcasts.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">Рассылок пока нет</div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {broadcasts.map(b => <BroadcastCard key={b.task_id} b={b} onRefresh={refreshOne} />)}
        </div>
      )}
    </div>
  );
}
