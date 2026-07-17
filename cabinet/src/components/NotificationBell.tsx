import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bell, BellOff, CheckCheck, Loader2, Trash2,
  ShieldAlert, Activity, Smartphone, CreditCard, Gift, Percent, type LucideIcon,
} from "lucide-react";
import { notificationsApi, type UserNotification } from "@/api/notifications";
import { formatRelativeOnline } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

// Центр уведомлений в кабинете: колокольчик со счётчиком непрочитанных + лента.
// Чинит «push в небытие» на мобиле — уведомления теперь можно посмотреть позже.

// Иконка + тон по смыслу уведомления (эвристика по тексту/ссылке).
function metaFor(n: UserNotification): { Icon: LucideIcon; cls: string } {
  const s = `${n.title} ${n.body} ${n.url}`.toLowerCase();
  if (/вход|sign|безопас|парол/.test(s)) return { Icon: ShieldAlert, cls: "bg-amber-500/15 text-amber-500" };
  if (/трафик|traffic|\bгб\b|лимит/.test(s)) return { Icon: Activity, cls: "bg-accent/15 text-accent" };
  if (/устройств|device/.test(s)) return { Icon: Smartphone, cls: "bg-accent/15 text-accent" };
  if (/подписк|тариф|продл|оплат|плат[её]ж|баланс|пополн|payment/.test(s)) return { Icon: CreditCard, cls: "bg-success/15 text-success" };
  if (/реферал|балл|бонус|начисл|подар|gift|к[еэ]шб[еэ]к/.test(s)) return { Icon: Gift, cls: "bg-accent/15 text-accent" };
  if (/скидк|промо|акци|promo/.test(s)) return { Icon: Percent, cls: "bg-amber-500/15 text-amber-500" };
  return { Icon: Bell, cls: "bg-accent/15 text-accent" };
}

export function NotificationBell() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<UserNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const wrapRef = useRef<HTMLDivElement>(null);

  const refreshCount = async () => {
    try {
      const { unread } = await notificationsApi.unreadCount();
      setUnread(unread);
    } catch {
      /* тихо */
    }
  };

  useEffect(() => {
    refreshCount();
    const iv = setInterval(refreshCount, 45000);
    return () => clearInterval(iv);
  }, []);

  const openPanel = async () => {
    setOpen(true);
    setLoading(true);
    try {
      const r = await notificationsApi.list();
      setItems(r.items);
      setUnread(r.unread);
    } catch {
      /* тихо */
    } finally {
      setLoading(false);
    }
  };

  const markAll = async () => {
    try {
      await notificationsApi.markRead();
    } catch {
      /* тихо */
    }
    setItems((prev) => prev.map((i) => ({ ...i, is_read: true })));
    setUnread(0);
  };

  const clearAll = async () => {
    try {
      await notificationsApi.clear();
    } catch {
      /* тихо */
    }
    setItems([]);
    setUnread(0);
  };

  const clickItem = async (n: UserNotification) => {
    if (!n.is_read) {
      try {
        await notificationsApi.markRead(n.id);
      } catch {
        /* тихо */
      }
      setUnread((u) => Math.max(0, u - 1));
      setItems((prev) => prev.map((i) => (i.id === n.id ? { ...i, is_read: true } : i)));
    }
    setOpen(false);
    if (n.url && n.url !== "/") navigate(n.url);
  };

  const hasUnread = items.some((i) => !i.is_read);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openPanel())}
        aria-label={t("notif.title")}
        className={`relative flex h-8 w-8 items-center justify-center rounded-lg transition-colors active:opacity-70 ${
          open ? "bg-accent-subtle text-accent" : "text-fg-muted hover:bg-bg-subtle hover:text-fg"
        }`}
      >
        <Bell className="h-5 w-5" strokeWidth={2} />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold leading-none text-white ring-2 ring-bg">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          {/* Backdrop — клик вне закрывает */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="surface animate-fade-in fixed inset-x-2 top-16 z-50 flex max-h-[72vh] flex-col overflow-hidden sm:absolute sm:inset-x-auto sm:right-0 sm:top-11 sm:w-[22rem]">
            {/* Header */}
            <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-fg">{t("notif.title")}</span>
                {unread > 0 && (
                  <span className="rounded-full bg-accent-subtle px-1.5 py-0.5 text-[10px] font-bold text-accent">
                    {t("notif.newCount", { n: unread })}
                  </span>
                )}
              </div>
              {items.length > 0 && (
                <button
                  type="button"
                  onClick={clearAll}
                  title={t("notif.clearAll")}
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-danger"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </div>

            {/* List */}
            <div className="min-h-0 flex-1 overflow-y-auto">
              {loading ? (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-5 w-5 animate-spin text-accent" />
                </div>
              ) : items.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                  <div className="flex h-12 w-12 items-center justify-center rounded-full bg-bg-subtle text-fg-subtle">
                    <BellOff className="h-6 w-6" />
                  </div>
                  <p className="text-sm font-medium text-fg">{t("notif.empty")}</p>
                  <p className="text-xs text-fg-muted">{t("notif.emptyHint")}</p>
                </div>
              ) : (
                <ul>
                  {items.map((n) => {
                    const { Icon, cls } = metaFor(n);
                    return (
                      <li key={n.id} className="border-b border-border-subtle last:border-0">
                        <button
                          type="button"
                          onClick={() => clickItem(n)}
                          className={`group flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-bg-subtle ${
                            n.is_read ? "" : "bg-accent-subtle"
                          }`}
                        >
                          <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${cls}`}>
                            <Icon className="h-[18px] w-[18px]" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className={`block truncate text-sm ${n.is_read ? "font-medium text-fg" : "font-bold text-fg"}`}>
                                {n.title}
                              </span>
                              {!n.is_read && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}
                            </span>
                            {n.body && <span className="mt-0.5 line-clamp-2 block text-xs leading-snug text-fg-muted">{n.body}</span>}
                            {n.created_at && <span className="mt-1 block text-[11px] text-fg-subtle">{formatRelativeOnline(n.created_at)}</span>}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {/* Footer */}
            {hasUnread && (
              <button
                type="button"
                onClick={markAll}
                className="flex items-center justify-center gap-1.5 border-t border-border-subtle px-4 py-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-subtle"
              >
                <CheckCheck className="h-4 w-4" />
                {t("notif.markAll")}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
