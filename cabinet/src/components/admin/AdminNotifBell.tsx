import { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { notificationsAdminApi } from "@/api/admin";

// Колокольчик уведомлений админа в верхней панели. Бейдж = число записей новее
// последнего просмотра центра (localStorage admin_notif_seen). Клик → центр,
// где отметка «просмотрено» обновляется и шлёт событие admin-notif-seen.
function seenTs(): number {
  try {
    return Number(localStorage.getItem("admin_notif_seen")) || 0;
  } catch {
    return 0;
  }
}

export function AdminNotifBell() {
  const navigate = useNavigate();
  const [unread, setUnread] = useState(0);

  const refresh = () => {
    notificationsAdminApi
      .list(50)
      .then((r) => {
        const ts = seenTs();
        setUnread(
          r.items.filter((it) => it.created_at && new Date(it.created_at).getTime() > ts).length,
        );
      })
      .catch(() => {});
  };

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 60000);
    const onSeen = () => setUnread(0);
    window.addEventListener("admin-notif-seen", onSeen);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("admin-notif-seen", onSeen);
    };
  }, []);

  return (
    <button
      type="button"
      onClick={() => navigate("/admin/notifications")}
      aria-label="Уведомления"
      title="Уведомления"
      className="relative flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
    >
      <Bell className="h-5 w-5" strokeWidth={1.75} />
      {unread > 0 && (
        <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold leading-none text-white">
          {unread > 9 ? "9+" : unread}
        </span>
      )}
    </button>
  );
}
