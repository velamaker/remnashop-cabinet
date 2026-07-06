import { useEffect, useState } from "react";
import { Bell } from "lucide-react";
import { pushApi } from "@/api/push";
import { useT } from "@/i18n/I18nContext";

// VAPID public key (base64url) → Uint8Array для applicationServerKey.
function urlBase64ToUint8Array(base64String: string): BufferSource {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const arr = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

const pushSupported =
  typeof navigator !== "undefined" &&
  "serviceWorker" in navigator &&
  typeof window !== "undefined" &&
  "PushManager" in window &&
  "Notification" in window;

/**
 * Тумблер Web Push уведомлений PWA. Просит разрешение, подписывается через
 * service worker (pushManager) с VAPID-ключом и шлёт подписку на бэкенд.
 * На iOS работает только в установленной на «экран Домой» PWA (16.4+).
 */
export function PushToggle() {
  const tr = useT();
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!pushSupported) {
      setReady(true);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (alive) setEnabled(!!sub);
      } catch {
        /* ignore */
      } finally {
        if (alive) setReady(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const enable = async () => {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") {
      setError(tr("push.denied"));
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const { public_key } = await pushApi.vapidKey();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    });
    const json = sub.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
    if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
      setError(tr("push.failed"));
      return;
    }
    await pushApi.subscribe({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
    });
    setEnabled(true);
  };

  const disable = async () => {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await pushApi.unsubscribe(sub.endpoint).catch(() => {});
      await sub.unsubscribe().catch(() => {});
    }
    setEnabled(false);
  };

  const toggle = async () => {
    setBusy(true);
    setError(null);
    setTestMsg(null);
    try {
      if (enabled) await disable();
      else await enable();
    } catch (e) {
      setError((e as Error)?.message || tr("push.failed"));
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    setBusy(true);
    setError(null);
    setTestMsg(null);
    try {
      await pushApi.test();
      setTestMsg(tr("push.testSent"));
    } catch (e) {
      setError((e as Error)?.message || tr("push.failed"));
    } finally {
      setBusy(false);
    }
  };

  if (!ready) return null;

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <Bell className="mt-0.5 h-4 w-4 flex-shrink-0 text-accent" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-fg">{tr("push.title")}</p>
            <p className="mt-0.5 text-xs text-fg-muted">
              {pushSupported ? tr("push.sub") : tr("push.unsupported")}
            </p>
          </div>
        </div>
        {pushSupported && (
          <button
            type="button"
            onClick={toggle}
            disabled={busy}
            role="switch"
            aria-checked={enabled}
            className={`relative h-6 w-11 flex-shrink-0 rounded-full transition-colors disabled:opacity-50 ${enabled ? "bg-accent" : "border border-[var(--border)] bg-bg-overlay"}`}
          >
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${enabled ? "left-[22px]" : "left-0.5"}`} />
          </button>
        )}
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      {testMsg && <p className="mt-2 text-xs text-success">{testMsg}</p>}
      {pushSupported && enabled && (
        <button
          type="button"
          onClick={sendTest}
          disabled={busy}
          className="mt-3 text-xs font-medium text-accent hover:underline disabled:opacity-50"
        >
          {tr("push.test")}
        </button>
      )}
    </div>
  );
}
