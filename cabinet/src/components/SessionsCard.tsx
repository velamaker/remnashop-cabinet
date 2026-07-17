import { useEffect, useState } from "react";
import { Monitor, LogOut, Loader2 } from "lucide-react";
import { sessionsApi, type SessionEntry } from "@/api/sessions";
import { formatRelativeOnline } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

type TFn = (key: string, vars?: Record<string, string | number>) => string;

// Бренды/ОС не переводятся; переводим только «Регистрация» (register).
function methodLabel(method: string, t: TFn): string {
  const brands: Record<string, string> = {
    email: "Email",
    telegram: "Telegram",
    telegram_oidc: "Telegram",
    telegram_webapp: "Telegram Mini App",
  };
  if (method === "register") return t("sessions.register");
  return brands[method] ?? method;
}

function shortUA(ua: string | null, t: TFn): string {
  if (!ua) return t("sessions.unknownDevice");
  if (/Happ/i.test(ua)) return "Happ";
  if (/iPhone|iPad|iOS/i.test(ua)) return "iOS";
  if (/Android/i.test(ua)) return "Android";
  if (/Windows/i.test(ua)) return "Windows";
  if (/Mac OS|Macintosh/i.test(ua)) return "macOS";
  if (/Chrome/i.test(ua)) return "Chrome";
  if (/Firefox/i.test(ua)) return "Firefox";
  if (/Safari/i.test(ua)) return "Safari";
  return ua.slice(0, 40);
}

interface DeviceRow {
  device: string;
  methods: string[]; // разные способы входа с этого устройства
  ip: string | null; // последний IP
  created_at: string | null; // последний вход
  count: number;
}

/**
 * Схлопывает журнал входов до ОДНОЙ строки на устройство (семейство ОС).
 * IP и метод НЕ входят в ключ: под VPN IP = exit-нода и постоянно меняется,
 * а один и тот же телефон логинится и через OIDC, и через Mini App — иначе
 * одно устройство дробится на десяток строк.
 */
function collapse(items: SessionEntry[], t: TFn): DeviceRow[] {
  const byKey = new Map<string, DeviceRow>();
  for (const s of items) {
    const device = shortUA(s.user_agent, t);
    const label = s.method ? methodLabel(s.method, t) : null;
    const existing = byKey.get(device);
    if (existing) {
      existing.count += 1; // items уже отсортированы DESC → первый = самый свежий
      if (label && !existing.methods.includes(label)) existing.methods.push(label);
    } else {
      byKey.set(device, { device, methods: label ? [label] : [], ip: s.ip, created_at: s.created_at, count: 1 });
    }
  }
  return [...byKey.values()];
}

/** Устройства, входившие после последнего «выйти со всех» + сам «выйти со всех». */
export function SessionsCard() {
  const t = useT();
  const [items, setItems] = useState<SessionEntry[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    sessionsApi.list().then((r) => setItems(r.items)).catch(() => setItems([]));
  }, []);

  if (items === null) return null;

  const devices = collapse(items, t);

  const logoutAll = async () => {
    if (!confirm(t("sessions.logoutConfirm"))) return;
    setBusy(true);
    try {
      await sessionsApi.logoutAll();
      window.location.href = "/login";
    } catch {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Monitor className="h-5 w-5 text-accent" />
          <h3 className="text-base font-bold text-fg">{t("sessions.title")}</h3>
        </div>
        <button
          type="button"
          onClick={logoutAll}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm font-medium text-danger hover:bg-danger/15 disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
          {t("sessions.logoutAll")}
        </button>
      </div>
      <p className="mt-1 text-xs text-fg-muted">{t("sessions.subtitle")}</p>

      {devices.length === 0 ? (
        <p className="mt-4 text-sm text-fg-muted">{t("sessions.empty")}</p>
      ) : (
        <div className="mt-4 space-y-2">
          {devices.map((s, i) => (
            <div key={i} className="flex items-center justify-between gap-3 rounded-xl border border-border-subtle bg-bg px-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg">
                  {s.device}
                  {s.methods.length > 0 && <span className="ml-2 text-xs text-fg-subtle">· {s.methods.join(", ")}</span>}
                </p>
                <p className="text-xs text-fg-muted">
                  {s.ip || "—"}
                  {s.count > 1 && <span className="ml-2 text-fg-subtle">· {t("sessions.loginsCount", { count: s.count })}</span>}
                </p>
              </div>
              <span className="shrink-0 text-xs text-fg-subtle">{s.created_at ? formatRelativeOnline(s.created_at) : ""}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
