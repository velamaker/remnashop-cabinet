import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import type { TicketMessage } from "@/api/support";

function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function TicketThread({
  messages,
  mySide,
  disabled,
  onSend,
}: {
  messages: TicketMessage[];
  /** Чья сторона выравнивается вправо. */
  mySide: "user" | "admin";
  disabled?: boolean;
  onSend: (body: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const handleSend = async () => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      await onSend(body);
      setText("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-1">
        {messages.map((m) => {
          const mine = m.sender === mySide;
          return (
            <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm ${
                  mine
                    ? "bg-accent text-accent-fg"
                    : "border border-[var(--border)] bg-bg-subtle text-fg"
                }`}
              >
                <p className="whitespace-pre-wrap break-words">{m.body}</p>
                <p className={`mt-1 text-[10px] ${mine ? "text-accent-fg/70" : "text-fg-subtle"}`}>
                  {m.sender === "admin" ? "Поддержка" : "Вы"} · {formatTime(m.created_at)}
                </p>
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>

      {!disabled && (
        <div className="mt-3 flex items-end gap-2 border-t border-[var(--border-subtle)] pt-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend();
            }}
            rows={2}
            placeholder="Напишите сообщение…"
            className="min-h-[40px] flex-1 resize-none rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-2 focus:ring-accent"
          />
          <button
            onClick={handleSend}
            disabled={!text.trim() || sending}
            className="btn-gradient inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl transition-all active:scale-95 disabled:opacity-50"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}
