import { useEffect, useState } from "react";
import { AlertCircle, Bell, Trash2, Smartphone, LayoutList } from "lucide-react";
import { notificationsAdminApi, type AdminNotification } from "@/api/admin";
import { useT } from "@/i18n/I18nContext";
import { activeLocale } from "@/lib/format";
import { ApiError } from "@/types/api";

function fmt(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(activeLocale(), {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AdminNotificationsPage() {
  const t = useT();
  const [items, setItems] = useState<AdminNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [pushEnabled, setPushEnabled] = useState<boolean | null>(null);
  const [pushSaving, setPushSaving] = useState(false);
  // null = бэкенд про rich-вид не знает (адаптер «Бедолаги» или старая версия
  // бота) → карточку не рисуем вовсе, мёртвого переключателя быть не должно.
  const [richEnabled, setRichEnabled] = useState<boolean | null>(null);
  const [richSaving, setRichSaving] = useState(false);

  const load = () => {
    setLoading(true);
    notificationsAdminApi
      .list(200)
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof ApiError ? e.detail : t("adm.notifications.err_generic")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    notificationsAdminApi
      .getSettings()
      .then((s) => {
        setPushEnabled(s.admin_push_enabled);
        // Только настоящий boolean включает карточку rich-вида: поля нет —
        // значит слоя на бэкенде нет (см. NotifSettings в api/admin.ts).
        setRichEnabled(typeof s.admin_rich_enabled === "boolean" ? s.admin_rich_enabled : null);
      })
      .catch(() => {});
    try { localStorage.setItem("admin_notif_seen", String(Date.now())); } catch { /* ignore */ }
    window.dispatchEvent(new Event("admin-notif-seen"));
  }, []);

  const togglePush = async () => {
    if (pushEnabled === null) return;
    const next = !pushEnabled;
    setPushSaving(true);
    setPushEnabled(next); // оптимистично
    try {
      const s = await notificationsAdminApi.updateSettings({ admin_push_enabled: next });
      setPushEnabled(s.admin_push_enabled);
    } catch (e) {
      setPushEnabled(!next); // откат
      setError(e instanceof ApiError ? e.detail : t("adm.notifications.err_save"));
    } finally {
      setPushSaving(false);
    }
  };

  const toggleRich = async () => {
    if (richEnabled === null) return;
    const next = !richEnabled;
    setRichSaving(true);
    setRichEnabled(next); // оптимистично
    try {
      const s = await notificationsAdminApi.updateSettings({ admin_rich_enabled: next });
      // Настройка хранится У БОТА (а кабинет может стоять на другом сервере),
      // поэтому верим ответу сервера, а не своему оптимистичному значению.
      setRichEnabled(typeof s.admin_rich_enabled === "boolean" ? s.admin_rich_enabled : next);
    } catch (e) {
      setRichEnabled(!next); // откат
      setError(e instanceof ApiError ? e.detail : t("adm.notifications.err_save"));
    } finally {
      setRichSaving(false);
    }
  };

  const clear = async () => {
    if (!confirm(t("adm.notifications.clear_confirm"))) return;
    setClearing(true);
    try {
      await notificationsAdminApi.clear();
      setItems([]);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.notifications.err_clear"));
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Bell className="h-5 w-5 text-fg-muted" />
          <h1 className="text-2xl font-bold text-fg">{t("adm.notifications.title")}</h1>
        </div>
        {items.length > 0 && (
          <button
            onClick={clear}
            disabled={clearing}
            className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-[var(--border)] bg-bg-raised px-3 py-2 text-sm font-medium text-fg-muted transition-colors hover:text-danger disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" />
            {t("adm.notifications.clear")}
          </button>
        )}
      </div>

      <p className="text-sm text-fg-muted">{t("adm.notifications.intro")}</p>

      {/* Тумблер: дублировать на телефон (web-push) */}
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-bg-subtle px-4 py-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-subtle text-accent">
            <Smartphone className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-fg">{t("adm.notifications.push_title")}</p>
            <p className="mt-0.5 text-xs text-fg-muted">{t("adm.notifications.push_hint")}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={togglePush}
          disabled={pushEnabled === null || pushSaving}
          aria-pressed={pushEnabled ?? false}
          className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
            pushEnabled ? "bg-accent" : "bg-border"
          }`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
              pushEnabled ? "translate-x-5" : "translate-x-0.5"
            }`}
          />
        </button>
      </div>

      {/* Тумблер: вид админских уведомлений в Telegram (карточка ↔ обычный текст).
          Показываем, только если бэкенд отдал поле: на установке поверх чужого
          бота этого слоя нет и управлять нечем. */}
      {richEnabled !== null && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-bg-subtle px-4 py-3">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-subtle text-accent">
              <LayoutList className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-semibold text-fg">{t("adm.notifications.rich_title")}</p>
              <p className="mt-0.5 text-xs text-fg-muted">{t("adm.notifications.rich_hint")}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={toggleRich}
            disabled={richSaving}
            aria-pressed={richEnabled}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
              richEnabled ? "bg-accent" : "bg-border"
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                richEnabled ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-20">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
        </div>
      ) : items.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.notifications.empty")}</div>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <div
              key={it.id}
              className="flex items-start gap-3 rounded-xl border border-border-subtle bg-bg-subtle px-4 py-3"
            >
              <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-subtle text-accent">
                <Bell className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                {it.title && <p className="text-sm font-semibold text-fg">{it.title}</p>}
                {it.body && <p className="mt-0.5 text-sm text-fg-muted">{it.body}</p>}
              </div>
              <span className="shrink-0 text-xs text-fg-subtle tabular">{fmt(it.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
