import { useCallback, useEffect, useState } from "react";
import { MessageCircle, Plus, X, Send, ArrowLeft } from "lucide-react";
import {
  supportApi,
  SUPPORT_TELEGRAM,
  type TicketListItem,
  type TicketDetail,
  type TicketStatus,
} from "@/api/support";
import { TicketThread } from "@/components/TicketThread";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/types/api";

const STATUS_META: Record<TicketStatus, { label: string; cls: string }> = {
  open: { label: "Открыт", cls: "bg-warning/10 text-warning" },
  answered: { label: "Отвечен", cls: "bg-success/10 text-success" },
  closed: { label: "Закрыт", cls: "bg-fg-subtle/15 text-fg-muted" },
};

function StatusPill({ status }: { status: TicketStatus }) {
  const m = STATUS_META[status];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${m.cls}`}>{m.label}</span>;
}

function NewTicketModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { id } = await supportApi.create(subject.trim(), message.trim());
      onCreated(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось создать обращение");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="surface w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">Новое обращение</h2>
          <button onClick={onClose} className="text-fg-subtle hover:text-fg">
            <X className="h-5 w-5" />
          </button>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            required
            minLength={2}
            maxLength={200}
            placeholder="Тема обращения"
            className="w-full rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2.5 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            required
            minLength={1}
            rows={5}
            placeholder="Опишите проблему подробно…"
            className="w-full resize-none rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2.5 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
          {error && <p className="text-sm text-danger">{error}</p>}
          <Button type="submit" isLoading={busy} className="self-end btn-gradient border-0">
            <Send className="h-4 w-4" />
            Отправить
          </Button>
        </form>
      </div>
    </div>
  );
}

export default function SupportPage() {
  const [tickets, setTickets] = useState<TicketListItem[]>([]);
  const [active, setActive] = useState<TicketDetail | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [showModal, setShowModal] = useState(false);

  const loadList = useCallback(async () => {
    setLoadingList(true);
    try {
      const { items } = await supportApi.list();
      setTickets(items);
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openTicket = async (id: number) => {
    const detail = await supportApi.get(id);
    setActive(detail);
  };

  const handleSend = async (body: string) => {
    if (!active) return;
    await supportApi.reply(active.id, body);
    await openTicket(active.id);
    loadList();
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-fg">Поддержка</h1>
        <Button onClick={() => setShowModal(true)} className="btn-gradient border-0">
          <Plus className="h-4 w-4" />
          Новый тикет
        </Button>
      </div>

      {/* Telegram fallback */}
      <div className="surface flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-subtle text-accent">
            <MessageCircle className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-medium text-fg">Связаться в Telegram</p>
            <p className="text-xs text-fg-muted">@{SUPPORT_TELEGRAM}</p>
          </div>
        </div>
        <a
          href={`https://t.me/${SUPPORT_TELEGRAM}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-9 items-center justify-center rounded-lg border border-[var(--border)] bg-bg-raised px-4 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay"
        >
          Открыть чат
        </a>
      </div>

      {/* Two-column */}
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        {/* List */}
        <div className={`surface p-4 ${active ? "hidden lg:block" : ""}`}>
          <h2 className="mb-3 text-sm font-semibold text-fg">Ваши обращения</h2>
          {loadingList ? (
            <p className="py-8 text-center text-sm text-fg-subtle">Загрузка…</p>
          ) : tickets.length === 0 ? (
            <div className="py-10 text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-bg-subtle text-fg-subtle">
                <MessageCircle className="h-6 w-6" />
              </div>
              <p className="text-sm text-fg-muted">Нет обращений</p>
            </div>
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
                  <span className="text-xs text-fg-subtle">#{t.id} · {t.messages_count} сообщ.</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Conversation */}
        <div className={`surface flex min-h-[420px] flex-col p-4 ${active ? "" : "hidden lg:flex"}`}>
          {active ? (
            <>
              <div className="mb-3 flex items-center gap-2 border-b border-[var(--border-subtle)] pb-3">
                <button onClick={() => setActive(null)} className="text-fg-subtle hover:text-fg lg:hidden">
                  <ArrowLeft className="h-5 w-5" />
                </button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-fg">{active.subject}</p>
                  <p className="text-xs text-fg-subtle">Обращение #{active.id}</p>
                </div>
                <StatusPill status={active.status} />
              </div>
              <div className="flex-1">
                <TicketThread
                  messages={active.messages}
                  mySide="user"
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
              <p className="text-sm text-fg-muted">Выберите обращение или создайте новое</p>
            </div>
          )}
        </div>
      </div>

      {showModal && (
        <NewTicketModal
          onClose={() => setShowModal(false)}
          onCreated={(id) => {
            setShowModal(false);
            loadList();
            openTicket(id);
          }}
        />
      )}
    </div>
  );
}
