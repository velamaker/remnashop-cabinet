import { useEffect, useState, useCallback } from "react";
import {
  Search, ChevronLeft, ChevronRight, AlertCircle, X,
  CalendarPlus, Trash2, Ban, CheckCircle, Gift, RefreshCw, Star, ChevronDown, ChevronUp, LogIn, Gauge,
} from "lucide-react";
import {
  usersAdminApi, subscriptionsAdminApi, plansAdminApi, grantsAdminApi,
  type AdminUser, type AdminUserDetail, type AdminSubscription, type AdminPlan,
  type LoginHistory, type TrafficByNode, type GrantCatalog, type GrantPreset, type UserGrant,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";
import { useAuth } from "@/contexts/AuthContext";

const LIMIT = 25;

// Значения совпадают с серверным enum Role: USER=1, PREVIEW=2 (read-only админ),
// ADMIN=3, DEV=4, OWNER=5, SYSTEM=6.
const ROLE_LABELS: Record<number, { label: string; cls: string }> = {
  1: { label: "Пользователь", cls: "text-fg-muted" },
  2: { label: "Админ (просмотр)", cls: "text-warning" },
  3: { label: "Администратор", cls: "text-warning" },
  4: { label: "Разработчик", cls: "text-accent" },
  5: { label: "Владелец", cls: "text-accent" },
  6: { label: "Система", cls: "text-accent" },
};


const STATUS_COLORS: Record<string, string> = {
  ACTIVE: "text-success",
  EXPIRED: "text-danger",
  DISABLED: "text-fg-muted",
  DELETED: "text-fg-subtle",
  LIMITED: "text-warning",
};

function Tag({ children, cls }: { children: React.ReactNode; cls?: string }) {
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium ${cls ?? "bg-bg-raised text-fg-muted border border-[var(--border)]"}`}>
      {children}
    </span>
  );
}

// ─── Subscription Panel ────────────────────────────────────────────────────

function SubscriptionPanel({ userId, onUpdated }: { userId: number; onUpdated: () => void }) {
  const [data, setData] = useState<{ current: AdminSubscription | null; history: AdminSubscription[] } | null>(null);
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [extendDays, setExtendDays] = useState("30");
  const [grantPlanId, setGrantPlanId] = useState("");
  const [grantDays, setGrantDays] = useState("30");
  const [pointsDelta, setPointsDelta] = useState("0");
  const [showHistory, setShowHistory] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      subscriptionsAdminApi.getUser(userId),
      plansAdminApi.list(),
    ]).then(([sub, p]) => {
      setData(sub);
      setPlans(p.items);
      if (p.items[0]) setGrantPlanId(String(p.items[0].id));
    }).catch(e => setErr(e instanceof ApiError ? e.detail : "Ошибка")).finally(() => setLoading(false));
  }, [userId]);

  useEffect(() => { load(); }, [load]);

  const run = async (fn: () => Promise<unknown>, label: string) => {
    setAction(label);
    setErr(null);
    try {
      await fn();
      load();
      onUpdated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setAction(null);
    }
  };

  const sub = data?.current;

  if (loading) return <div className="flex justify-center py-6"><div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" /></div>;

  return (
    <div className="space-y-4">
      {err && <p className="rounded-lg bg-danger/8 px-3 py-2 text-xs text-danger">{err}</p>}

      {/* Current subscription */}
      <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-4">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-fg-subtle">Текущая подписка</p>
        {sub ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-fg">{sub.plan_name ?? "—"}</span>
              <span className={`text-xs font-medium ${STATUS_COLORS[sub.status] ?? "text-fg-muted"}`}>{sub.status}</span>
            </div>
            {sub.expire_at && (
              <p className="text-xs text-fg-muted">
                Истекает: <span className="text-fg">{formatDate(sub.expire_at)}</span>
              </p>
            )}
            <div className="flex gap-3 text-xs text-fg-muted">
              <span>Трафик: {sub.traffic_limit === 0 ? "∞" : `${(sub.traffic_limit / 1024 ** 3).toFixed(0)} ГБ`}</span>
              <span>Устройств: {sub.device_limit === 0 ? "∞" : sub.device_limit}</span>
              {sub.is_trial && <Tag cls="bg-accent/8 text-accent border-accent/15">Пробная</Tag>}
            </div>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">Нет активной подписки</p>
        )}
      </div>

      {/* Actions */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Extend */}
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg"><CalendarPlus className="h-3.5 w-3.5 text-success" />Изменить срок</p>
          <div className="flex gap-2">
            <input type="number" min={-3650} max={3650} value={extendDays} onChange={e => setExtendDays(e.target.value)}
              placeholder="+30 или -7"
              className="h-8 w-20 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
            <span className="self-center text-xs text-fg-muted">дней</span>
            <button
              onClick={() => run(() => subscriptionsAdminApi.extend(userId, Number(extendDays)), "extend")}
              disabled={action !== null || !sub || !extendDays || Number(extendDays) === 0}
              className="ml-auto rounded-lg bg-success/10 px-3 py-1.5 text-xs font-medium text-success hover:bg-success/20 disabled:opacity-40 transition-colors"
            >
              {action === "extend" ? "…" : "Применить"}
            </button>
          </div>
          <p className="mt-1.5 text-[11px] text-fg-subtle">Плюс — продлить, минус — убавить.</p>
        </div>

        {/* Grant */}
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg"><Gift className="h-3.5 w-3.5 text-accent" />Выдать подписку</p>
          <div className="flex flex-col gap-2">
            <select value={grantPlanId} onChange={e => setGrantPlanId(e.target.value)}
              className="h-8 rounded-lg border border-[var(--border)] bg-bg px-2 text-xs text-fg focus:outline-none focus:ring-1 focus:ring-accent">
              {plans.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <div className="flex gap-2">
              <input type="number" min={1} max={3650} value={grantDays} onChange={e => setGrantDays(e.target.value)}
                className="h-8 w-20 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
              <span className="self-center text-xs text-fg-muted">дней</span>
              <button
                onClick={() => run(() => subscriptionsAdminApi.grant(userId, Number(grantPlanId), Number(grantDays)), "grant")}
                disabled={action !== null || !grantPlanId}
                className="ml-auto rounded-lg bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 disabled:opacity-40 transition-colors"
              >
                {action === "grant" ? "…" : "Выдать"}
              </button>
            </div>
          </div>
        </div>

        {/* Points */}
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg"><Star className="h-3.5 w-3.5 text-warning" />Баллы</p>
          <div className="flex gap-2">
            <input type="number" value={pointsDelta} onChange={e => setPointsDelta(e.target.value)}
              className="h-8 w-24 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder="+100 или -50" />
            <button
              onClick={() => run(() => subscriptionsAdminApi.addPoints(userId, Number(pointsDelta)), "points")}
              disabled={action !== null || !pointsDelta || Number(pointsDelta) === 0}
              className="ml-auto rounded-lg bg-warning/10 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/20 disabled:opacity-40 transition-colors"
            >
              {action === "points" ? "…" : "Применить"}
            </button>
          </div>
        </div>

        {/* Danger actions */}
        <div className="rounded-xl border border-[var(--border)] p-4">
          <p className="mb-2 text-xs font-semibold text-fg-subtle">Опасные действия</p>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => run(() => subscriptionsAdminApi.resetTrial(userId), "trial")}
              disabled={action !== null}
              className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg-raised disabled:opacity-40 transition-colors"
            >
              <RefreshCw className="h-3 w-3" />{action === "trial" ? "…" : "Сброс триала"}
            </button>
            <button
              onClick={() => run(() => subscriptionsAdminApi.disable(userId), "disable")}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-warning/20 bg-warning/8 px-2.5 py-1.5 text-xs text-warning hover:bg-warning/15 disabled:opacity-40 transition-colors"
            >
              <Ban className="h-3 w-3" />{action === "disable" ? "…" : "Отключить"}
            </button>
            <button
              onClick={() => { if (confirm("Удалить подписку? Это действие нельзя отменить.")) run(() => subscriptionsAdminApi.delete(userId), "delete"); }}
              disabled={action !== null || !sub}
              className="flex items-center gap-1 rounded-lg border border-danger/20 bg-danger/8 px-2.5 py-1.5 text-xs text-danger hover:bg-danger/15 disabled:opacity-40 transition-colors"
            >
              <Trash2 className="h-3 w-3" />{action === "delete" ? "…" : "Удалить"}
            </button>
          </div>
        </div>
      </div>

      {/* History */}
      {(data?.history?.length ?? 0) > 0 && (
        <div>
          <button onClick={() => setShowHistory(!showHistory)} className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg transition-colors">
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showHistory ? "rotate-180" : ""}`} />
            История подписок ({data!.history.length})
          </button>
          {showHistory && (
            <div className="mt-2 space-y-1">
              {data!.history.map(s => (
                <div key={s.id} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
                  <span className="text-fg-muted">{s.plan_name ?? "—"}</span>
                  <span className={STATUS_COLORS[s.status] ?? "text-fg-muted"}>{s.status}</span>
                  {s.expire_at && <span className="text-fg-subtle">{formatDate(s.expire_at)}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Login History ─────────────────────────────────────────────────────────

function LoginHistoryBlock({ userId }: { userId: number }) {
  const [data, setData] = useState<LoginHistory | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    usersAdminApi.logins(userId).then(setData).catch(() => setData(null));
  }, [userId]);

  if (!data || data.total === 0) return null;

  const methodLabel = (m: string | null) =>
    m === "telegram_oidc" ? "Telegram" :
    m === "telegram_webapp" ? "Telegram Mini App" :
    m === "telegram" ? "Telegram" :
    m === "register" ? "Регистрация" :
    m === "email" ? "Email" : (m ?? "—");

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 text-xs font-semibold text-fg">
        <span className="flex items-center gap-1.5"><LogIn className="h-3.5 w-3.5 text-accent" />История входов</span>
        <span className="font-normal text-fg-subtle">
          {data.total} входов · {data.distinct_ips} IP
          <ChevronDown className={`ml-1 inline h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-1">
          {data.items.map((e, i) => (
            <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-xs">
              <span className="text-fg-muted">{e.created_at ? formatDate(e.created_at) : "—"}</span>
              <span className="text-fg-subtle">{methodLabel(e.method)}</span>
              <span className="font-mono text-fg">{e.ip ?? "скрыт"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Traffic by node ───────────────────────────────────────────────────────

function fmtBytesRu(n: number): string {
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)} ТБ`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} ГБ`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} МБ`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} КБ`;
  return `${n} Б`;
}

function TrafficByNodeBlock({ userId }: { userId: number }) {
  const [data, setData] = useState<TrafficByNode | null>(null);
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState(30);

  useEffect(() => {
    setData(null);
    usersAdminApi.trafficByNode(userId, days).then(setData).catch(() => setData(null));
  }, [userId, days]);

  // Прячем блок только если панель явно вернула "нет данных о нодах".
  if (data && (!data.available || data.nodes.length === 0)) return null;

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 text-xs font-semibold text-fg">
        <span className="flex items-center gap-1.5"><Gauge className="h-3.5 w-3.5 text-accent" />Трафик по нодам</span>
        <span className="font-normal text-fg-subtle">
          {data ? `${fmtBytesRu(data.total)} за ${data.days} дн.` : "загрузка…"}
          <ChevronDown className={`ml-1 inline h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="flex gap-1.5">
            {[30, 60, 90].map(d => (
              <button key={d} onClick={() => setDays(d)}
                className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  days === d ? "border-accent bg-accent/10 text-accent" : "border-[var(--border)] text-fg-muted hover:text-fg"
                }`}>
                {d} дн.
              </button>
            ))}
          </div>
          {!data ? (
            <p className="py-2 text-center text-xs text-fg-subtle">загрузка…</p>
          ) : (
            <div className="space-y-1">
              {data.nodes.map((n, i) => {
                const pct = data.total > 0 ? Math.round((n.total / data.total) * 100) : 0;
                return (
                  <div key={i} className="rounded-lg border border-[var(--border)] px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-fg">
                        {n.country_code && <span className="mr-1 text-fg-muted">{n.country_code}</span>}{n.name}
                      </span>
                      <span className="flex-shrink-0 font-medium text-fg">{fmtBytesRu(n.total)}</span>
                    </div>
                    <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-bg-subtle">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Access grant (гранулярные роли) ───────────────────────────────────────

function AccessGrantBlock({ userId }: { userId: number }) {
  const [catalog, setCatalog] = useState<GrantCatalog | null>(null);
  const [grant, setGrant] = useState<UserGrant | null>(null);
  const [fullAccess, setFullAccess] = useState(false);
  const [canWrite, setCanWrite] = useState(true);
  const [secs, setSecs] = useState<Set<string>>(new Set());
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([grantsAdminApi.catalog(), grantsAdminApi.get(userId)])
      .then(([cat, g]) => {
        setCatalog(cat);
        setGrant(g);
        setFullAccess(g.full_access);
        setCanWrite(g.has_grant ? g.can_write : true);
        setSecs(new Set(g.sections));
        setExpires(g.expires_at ? g.expires_at.slice(0, 10) : "");
      })
      .catch(() => setMsg("Не удалось загрузить доступ"));
  }, [userId]);
  useEffect(() => { load(); }, [load]);

  if (!catalog || !grant) return null;

  const applyPreset = (p: GrantPreset) => { setFullAccess(p.full_access); setSecs(new Set(p.sections)); };
  const toggleSec = (k: string) =>
    setSecs(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; });

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await grantsAdminApi.set(userId, {
        full_access: fullAccess,
        can_write: canWrite,
        sections: fullAccess ? [] : Array.from(secs),
        expires_at: expires ? new Date(expires + "T23:59:59").toISOString() : null,
      });
      setMsg("Сохранено"); load();
    } catch (e) { setMsg(e instanceof ApiError ? e.detail : "Ошибка"); }
    finally { setBusy(false); }
  };
  const removeGrant = async () => {
    setBusy(true); setMsg(null);
    try { await grantsAdminApi.remove(userId); setMsg("Доступ убран"); load(); }
    catch (e) { setMsg(e instanceof ApiError ? e.detail : "Ошибка"); }
    finally { setBusy(false); }
  };

  const eff = grant.effective;
  const effLabel = !eff.allowed
    ? "нет доступа к админке"
    : (eff.full_access ? "полный доступ" : `разделов: ${eff.sections.length}`) +
      (eff.can_write ? "" : " · только просмотр");

  return (
    <div className="space-y-3 rounded-xl border border-[var(--border)] p-4">
      <div>
        <p className="text-xs font-semibold text-fg">Доступ к админке (роль)</p>
        <p className="mt-0.5 text-[11px] text-fg-subtle">
          Выберите пресет или отметьте разделы вручную. Пусто = нет доступа к админке.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {catalog.presets.map(p => (
          <button key={p.key} type="button" onClick={() => applyPreset(p)}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-fg-muted hover:bg-bg-subtle hover:text-fg">
            {p.label}
          </button>
        ))}
      </div>

      <label className="flex items-center gap-2 text-xs text-fg">
        <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={fullAccess}
          onChange={e => setFullAccess(e.target.checked)} />
        Полный доступ ко всем разделам
      </label>
      <label className="flex items-center gap-2 text-xs text-fg">
        <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={!canWrite}
          onChange={e => setCanWrite(!e.target.checked)} />
        Только просмотр (изменения запрещены)
      </label>

      {!fullAccess && (
        <div className="flex flex-wrap gap-1.5">
          {catalog.sections.map(s => (
            <button key={s.key} type="button" onClick={() => toggleSec(s.key)}
              className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                secs.has(s.key)
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-[var(--border)] text-fg-muted hover:bg-bg-subtle hover:text-fg"
              }`}>
              {s.label}
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 text-xs">
        <span className="text-fg-muted">Истекает:</span>
        <input type="date" value={expires} onChange={e => setExpires(e.target.value)}
          className="h-8 rounded-lg border border-[var(--border)] bg-bg px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
        {expires
          ? <button type="button" onClick={() => setExpires("")} className="text-fg-subtle hover:text-fg">бессрочно</button>
          : <span className="text-fg-subtle">бессрочно</span>}
      </div>

      {msg && <p className="text-xs text-fg-muted">{msg}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={save} disabled={busy}
          className="rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50">
          {busy ? "Сохранение…" : "Сохранить доступ"}
        </button>
        {grant.has_grant && (
          <button type="button" onClick={removeGrant} disabled={busy}
            className="rounded-lg border border-danger/20 bg-danger/8 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger/15 disabled:opacity-50">
            Убрать доступ
          </button>
        )}
        <span className="ml-auto text-[11px] text-fg-subtle">Сейчас: {effLabel}</span>
      </div>
    </div>
  );
}

// ─── User Detail Modal ─────────────────────────────────────────────────────

function UserDetailModal({ userId, onClose, onUpdated }: { userId: number; onClose: () => void; onUpdated: () => void }) {
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"info" | "sub" | "tx">("sub");
  const [saving, setSaving] = useState(false);
  const [discountPersonal, setDiscountPersonal] = useState("");
  const [discountPurchase, setDiscountPurchase] = useState("");
  const { isOwner } = useAuth();

  const load = useCallback(() => {
    setLoading(true);
    usersAdminApi.get(userId)
      .then(d => { setDetail(d); setDiscountPersonal(String(d.user.personal_discount)); setDiscountPurchase(String(d.user.purchase_discount)); })
      .catch(e => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [userId]);

  useEffect(() => { load(); }, [load]);

  const toggleBlock = async () => {
    if (!detail) return;
    const willBlock = !detail.user.is_blocked;
    if (willBlock && !confirm("Заблокировать пользователя?")) return;
    setSaving(true);
    try {
      await usersAdminApi.block(userId, willBlock);
      load(); onUpdated();
    } catch (e) { alert(e instanceof ApiError ? e.detail : "Ошибка"); }
    finally { setSaving(false); }
  };

  const saveDiscount = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      await usersAdminApi.setDiscount(userId, Number(discountPersonal), Number(discountPurchase));
      load(); onUpdated();
    } catch (e) { alert(e instanceof ApiError ? e.detail : "Ошибка"); }
    finally { setSaving(false); }
  };

  const u = detail?.user;
  const roleInfo = u ? (ROLE_LABELS[u.role] ?? { label: `Роль ${u.role}`, cls: "text-fg-muted" }) : null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-8 backdrop-blur-sm overflow-y-auto">
      <div className="w-full max-w-2xl rounded-xl border border-[var(--border)] bg-bg shadow-raised mb-10">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent text-sm font-semibold">
              {u?.name?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div>
              <p className="text-sm font-semibold text-fg">{u?.name ?? "Загрузка…"}</p>
              {u?.username && <p className="text-xs text-fg-muted">@{u.username}</p>}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg"><X className="h-5 w-5" /></button>
        </div>

        {loading ? (
          <div className="flex justify-center py-10"><div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
        ) : error ? (
          <p className="p-5 text-sm text-danger">{error}</p>
        ) : u && detail ? (
          <>
            {/* Tabs */}
            <div className="flex border-b border-[var(--border)] px-5">
              {(["sub", "info", "tx"] as const).map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={`mr-4 border-b-2 pb-2.5 pt-3 text-xs font-medium transition-colors ${tab === t ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg"}`}>
                  {t === "sub" ? "Подписка" : t === "info" ? "Профиль" : "Транзакции"}
                </button>
              ))}
            </div>

            <div className="p-5">
              {/* Subscription tab */}
              {tab === "sub" && <SubscriptionPanel userId={userId} onUpdated={() => { load(); onUpdated(); }} />}

              {/* Info tab */}
              {tab === "info" && (
                <div className="space-y-5">
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    {[
                      ["ID", String(u.id)],
                      ["Telegram ID", u.telegram_id ? String(u.telegram_id) : "—"],
                      ["Email", u.email ?? "—"],
                      ["Email верифицирован", u.is_email_verified ? "Да" : "Нет"],
                      ["Роль", roleInfo?.label ?? "—"],
                      ["Язык", u.language],
                      ["Реф. код", u.referral_code],
                      ["Баллы", String(u.points)],
                      ["Зарегистрирован", u.created_at ? formatDate(u.created_at) : "—"],
                      ["Пробный доступен", u.is_trial_available ? "Да" : "Нет"],
                      ["Последний вход", detail.logins?.last_login_at ? formatDate(detail.logins.last_login_at) : "—"],
                      ["Входов / уник. IP", detail.logins ? `${detail.logins.total} / ${detail.logins.distinct_ips}` : "—"],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border border-[var(--border)] p-3">
                        <p className="text-fg-subtle mb-0.5">{label}</p>
                        <p className="text-fg font-medium truncate">{value}</p>
                      </div>
                    ))}
                  </div>

                  {/* История входов */}
                  <LoginHistoryBlock userId={userId} />

                  {/* Трафик по нодам (живьём из панели) */}
                  <TrafficByNodeBlock userId={userId} />

                  {/* Discounts */}
                  <div className="rounded-xl border border-[var(--border)] p-4">
                    <p className="mb-3 text-xs font-semibold text-fg">Скидки</p>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="mb-1 block text-xs text-fg-muted">Постоянная %</label>
                        <input type="number" min={0} max={100} value={discountPersonal} onChange={e => setDiscountPersonal(e.target.value)}
                          className="h-8 w-full rounded-lg border border-[var(--border)] bg-bg px-3 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
                        <p className="mt-1 text-[11px] text-fg-subtle">Действует на все покупки</p>
                      </div>
                      <div>
                        <label className="mb-1 block text-xs text-fg-muted">На след. покупку %</label>
                        <input type="number" min={0} max={100} value={discountPurchase} onChange={e => setDiscountPurchase(e.target.value)}
                          className="h-8 w-full rounded-lg border border-[var(--border)] bg-bg px-3 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent" />
                        <p className="mt-1 text-[11px] text-fg-subtle">Разовая, сгорает после покупки</p>
                      </div>
                    </div>
                    <button onClick={saveDiscount} disabled={saving}
                      className="mt-3 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50 transition-colors">
                      {saving ? "Сохранение…" : "Сохранить скидки"}
                    </button>
                  </div>

                  {/* Доступ к админке (гранулярные роли) — только владелец, не для владельцев/системных */}
                  {isOwner && u.role != null && u.role < 5 && (
                    <AccessGrantBlock userId={userId} />
                  )}

                  {/* Block */}
                  <button onClick={toggleBlock} disabled={saving}
                    className={`flex items-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${u.is_blocked ? "border-success/20 bg-success/8 text-success hover:bg-success/15" : "border-danger/20 bg-danger/8 text-danger hover:bg-danger/15"}`}>
                    {u.is_blocked ? <><CheckCircle className="h-4 w-4" />Разблокировать</> : <><Ban className="h-4 w-4" />Заблокировать</>}
                  </button>
                </div>
              )}

              {/* Transactions tab */}
              {tab === "tx" && (
                <div className="space-y-1.5">
                  {detail.transactions.length === 0 ? (
                    <p className="py-6 text-center text-sm text-fg-muted">Транзакций нет</p>
                  ) : detail.transactions.map(tx => (
                    <div key={tx.payment_id} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2.5 text-xs">
                      <div>
                        <p className="text-fg font-medium">{tx.gateway_type} · {tx.purchase_type}</p>
                        {tx.created_at && <p className="text-fg-subtle mt-0.5">{formatDate(tx.created_at)}</p>}
                      </div>
                      <span className={`font-medium ${tx.status === "COMPLETED" ? "text-success" : tx.status === "PENDING" ? "text-warning" : "text-fg-muted"}`}>{tx.status}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}

// ─── Main Page ─────────────────────────────────────────────────────────────

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");      // "" = все, иначе число роли
  const [statusFilter, setStatusFilter] = useState("");  // "", "active", "blocked"
  const [sortBy, setSortBy] = useState("created_at");    // created_at | last_login | name
  const [sortOrder, setSortOrder] = useState("desc");    // asc | desc
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const { isReadonlyAdmin } = useAuth();

  const load = useCallback(() => {
    setLoading(true);
    usersAdminApi.list({
      limit: LIMIT, offset,
      search: search || undefined,
      role: roleFilter ? Number(roleFilter) : undefined,
      blocked: statusFilter === "blocked" ? true : statusFilter === "active" ? false : undefined,
      sort: sortBy, order: sortOrder,
    })
      .then(r => { setUsers(r.items); setTotal(r.total); })
      .catch(e => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [offset, search, roleFilter, statusFilter, sortBy, sortOrder]);

  useEffect(() => { load(); }, [load]);

  const handleSearch = (v: string) => { setSearch(v); setOffset(0); };
  const setFilter = (fn: () => void) => { fn(); setOffset(0); };

  const totalPages = Math.ceil(total / LIMIT);
  const page = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight text-fg">Пользователи</h1>
        <span className="text-sm text-fg-muted">{total} всего</span>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
        <input
          value={search} onChange={e => handleSearch(e.target.value)}
          placeholder="Поиск по имени, email, username…"
          className="h-9 w-full rounded-lg border border-[var(--border)] bg-bg pl-9 pr-3 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>

      {/* Фильтры и сортировка */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={roleFilter}
          onChange={e => setFilter(() => setRoleFilter(e.target.value))}
          className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">Все роли</option>
          <option value="1">Пользователь</option>
          <option value="2">Админ (просмотр)</option>
          <option value="3">Администратор</option>
          <option value="5">Владелец</option>
        </select>
        <select
          value={statusFilter}
          onChange={e => setFilter(() => setStatusFilter(e.target.value))}
          className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="">Все статусы</option>
          <option value="active">Активные</option>
          <option value="blocked">Заблокированные</option>
        </select>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-fg-subtle">Сортировка:</span>
          <select
            value={sortBy}
            onChange={e => setFilter(() => setSortBy(e.target.value))}
            className="h-9 rounded-lg border border-[var(--border)] bg-bg px-2.5 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-accent"
          >
            <option value="created_at">Дата регистрации</option>
            <option value="last_login">Последний вход</option>
            <option value="name">Имя</option>
          </select>
          <button
            type="button"
            onClick={() => setFilter(() => setSortOrder(o => o === "asc" ? "desc" : "asc"))}
            title={sortOrder === "asc" ? "По возрастанию" : "По убыванию"}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] bg-bg text-fg-muted hover:text-fg"
          >
            {sortOrder === "asc" ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {error && <div className="flex items-center gap-2 rounded-lg bg-danger/8 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-16"><div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] bg-bg-subtle">
                  {["Пользователь", "Email", "Роль", "Статус", "ID", "Регистрация", "Посл. вход"].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.map((u, i) => {
                  const rInfo = ROLE_LABELS[u.role] ?? { label: `${u.role}`, cls: "text-fg-muted" };
                  // У read-only id скрыт сервером (null) → карточку не открыть.
                  const clickable = u.id != null;
                  return (
                    <tr
                      key={u.id ?? `row-${i}`}
                      onClick={clickable ? () => setSelectedId(u.id) : undefined}
                      className={`border-b border-[var(--border)] transition-colors ${clickable ? "cursor-pointer hover:bg-bg-subtle" : ""} ${i === users.length - 1 ? "border-0" : ""}`}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-bg-raised border border-[var(--border)] text-xs font-medium text-fg-muted">
                            {u.name?.[0]?.toUpperCase() ?? "?"}
                          </div>
                          <div className="min-w-0">
                            <p className="truncate font-medium text-fg">{u.name}</p>
                            {u.username && <p className="text-xs text-fg-subtle">@{u.username}</p>}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.email ?? "—"}</td>
                      <td className={`px-4 py-3 text-xs font-medium ${rInfo.cls}`}>{rInfo.label}</td>
                      <td className="px-4 py-3">
                        {u.is_blocked ? (
                          <span className="text-xs font-medium text-danger">Заблокирован</span>
                        ) : (
                          <span className="text-xs font-medium text-success">Активен</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-fg-muted font-mono">{u.id ?? "—"}</td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.created_at ? formatDate(u.created_at) : "—"}</td>
                      <td className="px-4 py-3 text-xs text-fg-muted">{u.last_login_at ? formatDate(u.last_login_at) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm">
              <button onClick={() => setOffset(Math.max(0, offset - LIMIT))} disabled={offset === 0}
                className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg disabled:opacity-40 transition-colors">
                <ChevronLeft className="h-3.5 w-3.5" /> Назад
              </button>
              <span className="text-xs text-fg-muted">Стр. {page} из {totalPages}</span>
              <button onClick={() => setOffset(offset + LIMIT)} disabled={offset + LIMIT >= total}
                className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg disabled:opacity-40 transition-colors">
                Вперёд <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </>
      )}

      {selectedId !== null && (
        <UserDetailModal userId={selectedId} onClose={() => setSelectedId(null)} onUpdated={load} />
      )}
    </div>
  );
}
