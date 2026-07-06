import { useEffect, useState, useCallback } from "react";
import { AlertCircle, ShieldAlert, Wifi, Mail, Users, Ban, TicketX, RefreshCw, Smartphone } from "lucide-react";
import {
  abuseAdminApi,
  usersAdminApi,
  type AbuseCluster,
  type AbuseAccount,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { useAuth } from "@/contexts/AuthContext";

const SIGNAL_META: Record<AbuseCluster["signal"], { label: string; icon: typeof Wifi }> = {
  ip: { label: "Общий IP", icon: Wifi },
  hwid: { label: "Общий девайс (HWID)", icon: Smartphone },
  email: { label: "Похожий email", icon: Mail },
  referral: { label: "Само-реферал", icon: Users },
};

const SEVERITY_META: Record<AbuseCluster["severity"], { label: string; cls: string }> = {
  high: { label: "Высокий", cls: "bg-danger/10 text-danger" },
  medium: { label: "Средний", cls: "bg-warning/10 text-warning" },
  low: { label: "Низкий", cls: "bg-fg-subtle/20 text-fg-muted" },
};

function accountLabel(a: AbuseAccount): string {
  if (a.username) return `@${a.username}`;
  if (a.email) return a.email;
  if (a.telegram_id) return `tg:${a.telegram_id}`;
  return a.name || `#${a.id}`;
}

export default function AdminAbusePage() {
  const { isReadonlyAdmin } = useAuth();
  const [clusters, setClusters] = useState<AbuseCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [onlyTrial, setOnlyTrial] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    abuseAdminApi
      .trials({ only_trial: onlyTrial })
      .then((r) => setClusters(r.clusters))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [onlyTrial]);

  useEffect(() => { load(); }, [load]);

  // Мутируем счёт локально после действия, чтобы не перезагружать весь список.
  const patchAccount = (id: number, patch: Partial<AbuseAccount>) =>
    setClusters((cs) =>
      cs.map((c) => ({
        ...c,
        accounts: c.accounts.map((a) => (a.id === id ? { ...a, ...patch } : a)),
      })),
    );

  const block = async (a: AbuseAccount) => {
    setBusy(a.id);
    try {
      const r = await usersAdminApi.block(a.id, !a.is_blocked);
      patchAccount(a.id, { is_blocked: r.is_blocked });
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setBusy(null);
    }
  };

  const denyTrial = async (a: AbuseAccount) => {
    setBusy(a.id);
    try {
      const r = await usersAdminApi.setTrial(a.id, !a.is_trial_available);
      patchAccount(a.id, {
        is_trial_available: r.is_trial_available,
        trial_used: !r.is_trial_available,
      });
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-fg">
          <ShieldAlert className="h-6 w-6 text-warning" />
          Детект абьюза
        </h1>
        <button
          onClick={load}
          className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] px-3 py-2 text-sm font-medium text-fg-muted hover:text-fg"
        >
          <RefreshCw className="h-4 w-4" />
          Обновить
        </button>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-accent/5 px-5 py-4 text-sm text-fg-muted">
        💡 Группы аккаунтов с совпадающими признаками (общий девайс/HWID, общий IP,
        «одинаковый» email с учётом gmail-точек/алиасов, само-рефералы с общего IP).
        Похоже на мультиаккаунт ради нескольких бесплатных пробников. HWID снимается с
        панели раз в 6 часов. Автодействий нет — решение за вами.
      </div>

      <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          checked={onlyTrial}
          onChange={(e) => setOnlyTrial(e.target.checked)}
          className="h-4 w-4 accent-[var(--accent)]"
        />
        Показывать только группы, где ≥2 аккаунтов уже взяли триал
      </label>

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
      ) : clusters.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">
          Подозрительных групп не найдено 🎉
        </div>
      ) : (
        <div className="space-y-4">
          {clusters.map((c, idx) => {
            const meta = SIGNAL_META[c.signal];
            const sev = SEVERITY_META[c.severity];
            const Icon = meta.icon;
            return (
              <div key={`${c.signal}:${c.key}:${idx}`} className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-fg">
                    <Icon className="h-4 w-4 text-fg-muted" />
                    {meta.label}
                  </span>
                  <code className="rounded bg-fg-subtle/10 px-1.5 py-0.5 text-xs text-fg-muted">{c.key}</code>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sev.cls}`}>{sev.label}</span>
                  <span className="ml-auto text-xs text-fg-subtle">{c.accounts.length} акк.</span>
                </div>

                <div className="space-y-1.5">
                  {c.accounts.map((a) => (
                    <div
                      key={a.id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-[var(--border)] bg-bg px-3 py-2 text-sm"
                    >
                      <span className="font-medium text-fg">{accountLabel(a)}</span>
                      <span className="text-xs text-fg-subtle">#{a.id}</span>
                      {a.trial_used && (
                        <span className="rounded bg-warning/10 px-1.5 text-xs text-warning">триал использован</span>
                      )}
                      {a.young_tg && (
                        <span className="rounded bg-fg-subtle/15 px-1.5 text-xs text-fg-muted">свежий TG</span>
                      )}
                      {a.is_blocked && (
                        <span className="rounded bg-danger/10 px-1.5 text-xs text-danger">заблокирован</span>
                      )}
                      {!isReadonlyAdmin && (
                        <div className="ml-auto flex items-center gap-2">
                          <button
                            onClick={() => denyTrial(a)}
                            disabled={busy === a.id}
                            title={a.is_trial_available ? "Снять право на триал" : "Вернуть право на триал"}
                            className="inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-warning disabled:opacity-50"
                          >
                            <TicketX className="h-3.5 w-3.5" />
                            {a.is_trial_available ? "Снять триал" : "Вернуть триал"}
                          </button>
                          <button
                            onClick={() => block(a)}
                            disabled={busy === a.id}
                            title={a.is_blocked ? "Разблокировать" : "Заблокировать"}
                            className="inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-danger disabled:opacity-50"
                          >
                            <Ban className="h-3.5 w-3.5" />
                            {a.is_blocked ? "Разбл." : "Блок"}
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
