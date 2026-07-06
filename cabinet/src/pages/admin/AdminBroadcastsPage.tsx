import { useEffect, useState, useCallback } from "react";
import { RefreshCw, AlertCircle, CheckCircle, XCircle, Clock, Send, Eye, EyeOff } from "lucide-react";
import { broadcastsAdminApi, type AdminBroadcast, type BroadcastChannel } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";

// Предпросмотр «как в Telegram»: экранируем всё, затем возвращаем только
// разрешённый Telegram whitelist тегов (b/i/u/s/code/pre/a). Скрипты/атрибуты
// не проходят — dangerouslySetInnerHTML безопасен.
function toPreviewHtml(raw: string): string {
  let s = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  s = s.replace(/&lt;(\/?)(b|strong|i|em|u|s|code|pre)&gt;/gi, "<$1$2>");
  s = s.replace(
    /&lt;a href="([^"]*)"&gt;/gi,
    '<a href="$1" target="_blank" rel="noreferrer" class="text-accent underline">',
  );
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
      { key: "TG_SUBSCRIBED", label: "С подпиской", hint: "активная (вкл. пробные)" },
      { key: "TG_UNSUBSCRIBED", label: "Без подписки", hint: "нет активной подписки" },
      { key: "TG_TRIAL", label: "Пробный период", hint: "сейчас на триале" },
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

function CreateBroadcast({ onCreated }: { onCreated: () => void }) {
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Set<BroadcastChannel>>(new Set());
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [sending, setSending] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);

  useEffect(() => {
    broadcastsAdminApi.audienceCounts().then(setCounts).catch(() => {});
  }, []);

  // Внутри одного канала «Все» и сегменты по статусу взаимоисключимы: «Все» —
  // надмножество сегментов, иначе часть юзеров получит рассылку дважды.
  // Telegram и Email независимы друг от друга.
  const conflicts = (k: BroadcastChannel, s: Set<BroadcastChannel>): boolean => {
    const ch = channelOf(k);
    const same = [...s].filter((x) => channelOf(x) === ch);
    if (isAll(k)) return same.some((x) => !isAll(x));
    return same.some((x) => isAll(x));
  };

  const toggle = (k: BroadcastChannel) => {
    if (!selected.has(k) && conflicts(k, selected)) return; // заблокирован — игнор
    setConfirm(false);
    setMsg(null);
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  };

  const recipients = [...selected].reduce((s, k) => s + (counts?.[k] ?? 0), 0);

  const submit = async () => {
    setErr(null);
    setMsg(null);
    if (!text.trim()) return setErr("Введите текст сообщения");
    if (selected.size === 0) return setErr("Выберите хотя бы один канал");
    if (!confirm) return setConfirm(true);
    setSending(true);
    try {
      await broadcastsAdminApi.create(text.trim(), [...selected]);
      setMsg("Рассылка запущена — прогресс появится в истории ниже");
      setText("");
      setSelected(new Set());
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
              {group.items.map((c) => {
                const on = selected.has(c.key);
                const blocked = !on && conflicts(c.key, selected);
                const cnt = counts?.[c.key];
                return (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => toggle(c.key)}
                    disabled={blocked}
                    title={blocked ? "Нельзя вместе с «Все» этого канала (задвоение получателей)" : undefined}
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
            {AUDIENCE_LABELS[b.audience] ?? b.audience}
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
    } catch {}
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
