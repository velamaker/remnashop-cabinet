import { useCallback, useEffect, useState } from "react";
import { MessageCircle, ArrowLeft, CheckCheck } from "lucide-react";
import {
  supportAdminApi,
  type AdminTicketListItem,
  type AdminTicketDetail,
  type TicketStatus,
} from "@/api/support";
import { TicketThread } from "@/components/TicketThread";

const STATUS_META: Record<TicketStatus, { label: string; cls: string }> = {
  open: { label: "Открыт", cls: "bg-warning/10 text-warning" },
  answered: { label: "Отвечен", cls: "bg-success/10 text-success" },
  closed: { label: "Закрыт", cls: "bg-fg-subtle/15 text-fg-muted" },
};

function StatusPill({ status }: { status: TicketStatus }) {
  const m = STATUS_META[status];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${m.cls}`}>{m.label}</span>;
}

const FILTERS: { value: TicketStatus | "all"; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "open", label: "Открытые" },
  { value: "answered", label: "Отвеченные" },
  { value: "closed", label: "Закрытые" },
];

function userLabel(u: AdminTicketListItem["user"]): string {
  return u.name || u.email || (u.telegram_id ? `TG ${u.telegram_id}` : `#${u.id}`);
}

export default function AdminSupportPage() {
  const [tickets, setTickets] = useState<AdminTicketListItem[]>([]);
  const [active, setActive] = useState<AdminTicketDetail | null>(null);
  const [filter, setFilter] = useState<TicketStatus | "all">("all");
  const [loading, setLoading] = useState(true);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const { items } = await supportAdminApi.list(filter === "all" ? undefined : filter);
      setTickets(items);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openTicket = async (id: number) => {
    setActive(await supportAdminApi.get(id));
  };

  const handleSend = async (body: string) => {
    if (!active) return;
    await supportAdminApi.reply(active.id, body);
    await openTicket(active.id);
    loadList();
  };

  const handleClose = async () => {
    if (!active) return;
    await supportAdminApi.setStatus(active.id, "closed");
    await openTicket(active.id);
    loadList();
  };

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-2xl font-bold text-fg">Поддержка</h1>

      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              filter === f.value
                ? "bg-accent text-accent-fg"
                : "border border-[var(--border)] bg-bg-raised text-fg-muted hover:text-fg"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        {/* List */}
        <div className={`surface min-w-0 p-4 ${active ? "hidden lg:block" : ""}`}>
          <h2 className="mb-3 text-sm font-semibold text-fg">
            Тикеты {tickets.length > 0 && <span className="text-fg-subtle">({tickets.length})</span>}
          </h2>
          {loading ? (
            <p className="py-8 text-center text-sm text-fg-subtle">Загрузка…</p>
          ) : tickets.length === 0 ? (
            <p className="py-10 text-center text-sm text-fg-muted">Тикетов нет</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {tickets.map((t) => (
                <button
                  key={t.id}
                  onClick={() => openTicket(t.id)}
                  className={`flex flex-col gap-1 rounded-xl border p-3 text-left transition-colors ${
                    active?.id === t.id
                      ? "border-accent/40 bg-accent-subtle"
                      : "border-[var(--border-subtle)] bg-bg-subtle hover:border-[var(--border)]"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-fg">{t.subject}</span>
                    <StatusPill status={t.status} />
                  </div>
                  <span className="truncate text-xs text-fg-subtle">
                    #{t.id} · {userLabel(t.user)} · {t.messages_count} сообщ.
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Conversation */}
        <div className={`surface flex min-h-[460px] min-w-0 flex-col overflow-hidden p-4 ${active ? "" : "hidden lg:flex"}`}>
          {active ? (
            <>
              <div className="mb-3 flex items-center gap-2 border-b border-[var(--border-subtle)] pb-3">
                <button onClick={() => setActive(null)} className="text-fg-subtle hover:text-fg lg:hidden">
                  <ArrowLeft className="h-5 w-5" />
                </button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-fg">{active.subject}</p>
                  <p className="truncate text-xs text-fg-subtle">
                    #{active.id} · {userLabel(active.user)}
                    {active.user.email ? ` · ${active.user.email}` : ""}
                  </p>
                </div>
                <StatusPill status={active.status} />
                {active.status !== "closed" && (
                  <button
                    onClick={handleClose}
                    title="Закрыть тикет"
                    className="inline-flex h-8 items-center gap-1 rounded-lg border border-[var(--border)] bg-bg-raised px-2.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
                  >
                    <CheckCheck className="h-3.5 w-3.5" />
                    Закрыть
                  </button>
                )}
              </div>
              <div className="flex-1">
                <TicketThread
                  messages={active.messages}
                  mySide="admin"
                  disabled={active.status === "closed"}
                  onSend={handleSend}
                />
              </div>
            </>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-bg-subtle text-fg-subtle">
                <MessageCircle className="h-7 w-7" />
              </div>
              <p className="text-sm text-fg-muted">Выберите тикет</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
