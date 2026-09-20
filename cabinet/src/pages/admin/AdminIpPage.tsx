import { useEffect, useState } from "react";
import { ShieldCheck, Save, Plus, AlertTriangle } from "lucide-react";
import { adminIpApi, type AdminIpConfig } from "@/api/admin";
import { ApiError } from "@/types/api";
import { TwoFactorCard } from "@/components/admin/Admin2FA";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

// Одна фраза — один ключ: чип с путём к файлу <code>…</code> живёт ВНУТРИ
// перевода. Так переводчик сам решает порядок слов, а предложение не
// собирается из кусков.
function withMarkup(s: string) {
  return s
    .split(/(<code>.*?<\/code>)/g)
    .map((part, i) => (part.startsWith("<code>") ? <code key={i}>{part.slice(6, -7)}</code> : part));
}

// Безопасность админки: 2FA (все админы) + ограничение по IP (только владелец).
export default function AdminIpPage() {
  const t = useT();
  const [cfg, setCfg] = useState<AdminIpConfig | null>(null);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminIpApi
      .get()
      .then((c) => {
        setCfg(c);
        setText((c.allowed_ips || []).join("\n"));
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : translate("adm.ip.load_error")));
  }, []);

  const addMyIp = () => {
    if (!cfg?.your_ip) return;
    const list = text.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!list.includes(cfg.your_ip)) setText([...list, cfg.your_ip].join("\n"));
  };

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const ips = text.split("\n").map((s) => s.trim()).filter(Boolean);
      const updated = await adminIpApi.update({ enabled: cfg.enabled, allowed_ips: ips });
      setCfg({ ...updated, your_ip: cfg.your_ip });
      setText((updated.allowed_ips || []).join("\n"));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.ip.save_error"));
    } finally {
      setSaving(false);
    }
  };

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <ShieldCheck className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">{t("adm.ip.title")}</h1>
      </div>

      {/* 2FA — для любого админа */}
      <TwoFactorCard />

      {/* Ограничение по IP — только владелец (если не владелец, cfg не загрузится) */}
      {cfg && (
      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <h3 className="mb-3 text-base font-bold text-fg">{t("adm.ip.section_title")}</h3>
        <label className="flex cursor-pointer items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-fg">{t("adm.ip.enable")}</p>
            <p className="mt-0.5 text-xs text-fg-muted">{t("adm.ip.enable_hint")}</p>
          </div>
          <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} className="h-5 w-5 accent-[var(--accent)]" />
        </label>

        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs text-fg-muted">{t("adm.ip.allowed_label")}</label>
            {cfg.your_ip && (
              <button type="button" onClick={addMyIp} className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
                <Plus className="h-3.5 w-3.5" /> {t("adm.ip.add_my_ip", { ip: cfg.your_ip })}
              </button>
            )}
          </div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} placeholder="203.0.113.5&#10;198.51.100.7" className={inputCls} />
        </div>

        <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-400/40 bg-amber-400/10 p-3 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{withMarkup(t("adm.ip.warning"))}</span>
        </div>

        <div className="mt-4 flex items-center justify-between gap-3">
          <span className="text-xs">
            {error && <span className="text-danger">{error}</span>}
            {saved && <span className="text-success">{t("adm.ip.saved")}</span>}
          </span>
          <button onClick={save} disabled={saving} className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            <Save className="h-4 w-4" /> {saving ? "…" : t("adm.ip.save")}
          </button>
        </div>
      </section>
      )}
    </div>
  );
}
