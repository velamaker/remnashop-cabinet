import { useEffect, useState, useCallback, useRef } from "react";
import {
  Server, Cpu, MemoryStick, Clock, RefreshCw, Power, PowerOff,
  Activity, Globe, Layers, AlertCircle, Users, Wifi,
} from "lucide-react";
import { adminApi } from "@/api/admin";

// ─── Types ────────────────────────────────────────────────────────────────────

interface SystemStats {
  metadata: { version: string; build?: string };
  stats: {
    cpu?: { cores: number; physical_cores?: number | null };
    memory?: { total: number; used: number; free: number };
    uptime?: number;
    users?: { total_users: number; status_counts?: Record<string, number> };
    online_stats?: { online_now: number; last_day: number; last_week: number; never_online: number };
    nodes?: { total_online: number; total_bytes_lifetime?: string };
  };
}

interface RwNode {
  uuid: string;
  name: string;
  address: string;
  port?: number;
  is_connected: boolean;
  is_disabled: boolean;
  is_connecting?: boolean;
  users_online: number;
  traffic_used_bytes?: number;
  traffic_limit_bytes?: number;
  xray_uptime?: number;
  country_code?: string;
  cpu_count?: number | null;
  cpu_model?: string | null;
  total_ram?: number | null;
  last_status_message?: string | null;
  xray_version?: string | null;
  node_version?: string | null;
  updated_at?: string;
}

interface RwHost {
  uuid: string;
  remark: string;
  address: string;
  port: number;
  is_disabled?: boolean;
  security_layer?: string;
  inbound?: { tag?: string; type?: string } | null;
  nodes?: { name: string }[];
  tag?: string;
}

interface RwInbound {
  uuid: string;
  tag: string;
  type: string;
  network?: string;
  security?: string;
  port?: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtBytes(n: number): string {
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)} ТБ`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} ГБ`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)} МБ`;
  return `${(n / 1e3).toFixed(0)} КБ`;
}

function fmtUptime(secs: number): string {
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return [d && `${d}д`, h && `${h}ч`, m && `${m}м`].filter(Boolean).join(" ") || "< 1м";
}

function StatCard({ icon: Icon, label, value, sub, accent }: {
  icon: React.ElementType; label: string; value: string; sub?: string; accent?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-4">
      <div className="mb-2.5 flex items-center gap-1.5 text-xs text-fg-muted">
        <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />{label}
      </div>
      <p className={`text-2xl font-bold tracking-tight ${accent ?? "text-fg"}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-fg-subtle">{sub}</p>}
    </div>
  );
}

// ─── Node Card ────────────────────────────────────────────────────────────────

function NodeCard({ node, onAction }: {
  node: RwNode;
  onAction: (uuid: string, action: "restart" | "enable" | "disable") => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (action: "restart" | "enable" | "disable") => {
    setBusy(action);
    try { await onAction(node.uuid, action); } finally { setBusy(null); }
  };

  const isOnline = node.is_connected && !node.is_disabled;
  const isConnecting = node.is_connecting && !node.is_connected;
  const memPct = node.total_ram && node.total_ram > 0 ? null : null;

  let statusLabel = "Офлайн";
  let statusCls = "bg-danger/10 text-danger";
  let dotCls = "bg-danger";
  if (node.is_disabled) { statusLabel = "Отключён"; statusCls = "bg-fg-subtle/10 text-fg-subtle"; dotCls = "bg-fg-subtle"; }
  else if (isOnline) { statusLabel = "Онлайн"; statusCls = "bg-success/10 text-success"; dotCls = "bg-success"; }
  else if (isConnecting) { statusLabel = "Подключение"; statusCls = "bg-warning/10 text-warning"; dotCls = "bg-warning"; }

  return (
    <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-4 flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`mt-0.5 h-2 w-2 flex-shrink-0 rounded-full ${dotCls} ${isOnline ? "animate-pulse" : ""}`} />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-fg">
              {node.country_code && <span className="mr-1 text-fg-muted">{node.country_code}</span>}
              {node.name}
            </p>
            <p className="truncate text-xs text-fg-subtle">{node.address}{node.port ? `:${node.port}` : ""}</p>
          </div>
        </div>
        <span className={`flex-shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${statusCls}`}>{statusLabel}</span>
      </div>

      {/* Online users — prominent */}
      <div className="flex items-center justify-between rounded-lg bg-bg px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs text-fg-muted"><Wifi className="h-3.5 w-3.5" />Онлайн сейчас</span>
        <span className={`text-sm font-bold ${node.users_online > 0 ? "text-success" : "text-fg-subtle"}`}>{node.users_online}</span>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 gap-2">
        {node.traffic_used_bytes != null && (
          <div className="rounded-lg bg-bg px-2.5 py-2">
            <p className="text-[10px] text-fg-subtle">Трафик</p>
            <p className="text-xs font-semibold text-fg">{fmtBytes(node.traffic_used_bytes)}</p>
          </div>
        )}
        {node.xray_uptime != null && (
          <div className="rounded-lg bg-bg px-2.5 py-2">
            <p className="text-[10px] text-fg-subtle">Аптайм</p>
            <p className="text-xs font-semibold text-fg">{fmtUptime(node.xray_uptime)}</p>
          </div>
        )}
        {node.cpu_count != null && (
          <div className="rounded-lg bg-bg px-2.5 py-2">
            <p className="text-[10px] text-fg-subtle">ЦПУ</p>
            <p className="text-xs font-semibold text-fg">{node.cpu_count} ядер</p>
          </div>
        )}
        {node.total_ram != null && node.total_ram > 0 && (
          <div className="rounded-lg bg-bg px-2.5 py-2">
            <p className="text-[10px] text-fg-subtle">ОЗУ</p>
            <p className="text-xs font-semibold text-fg">{fmtBytes(node.total_ram)}</p>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-2 pt-1">
        <button onClick={() => act("restart")} disabled={busy !== null || node.is_disabled}
          className="flex items-center gap-1 rounded-lg border border-[var(--border)] px-2.5 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-bg disabled:opacity-40 transition-colors">
          <RefreshCw className={`h-3 w-3 ${busy === "restart" ? "animate-spin" : ""}`} />
          {busy === "restart" ? "…" : "Рестарт"}
        </button>
        {node.is_disabled ? (
          <button onClick={() => act("enable")} disabled={busy !== null}
            className="flex items-center gap-1 rounded-lg border border-success/20 bg-success/8 px-2.5 py-1.5 text-xs text-success hover:bg-success/15 disabled:opacity-40 transition-colors">
            <Power className="h-3 w-3" />{busy === "enable" ? "…" : "Включить"}
          </button>
        ) : (
          <button onClick={() => act("disable")} disabled={busy !== null}
            className="flex items-center gap-1 rounded-lg border border-warning/20 bg-warning/8 px-2.5 py-1.5 text-xs text-warning hover:bg-warning/15 disabled:opacity-40 transition-colors">
            <PowerOff className="h-3 w-3" />{busy === "disable" ? "…" : "Выключить"}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Auto-refresh timer ───────────────────────────────────────────────────────

function RefreshTimer({ interval, onTick }: { interval: number; onTick: () => void }) {
  const [remaining, setRemaining] = useState(interval);
  const ref = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setRemaining(interval);
    const tick = setInterval(() => {
      setRemaining(r => {
        if (r <= 1) { onTick(); return interval; }
        return r - 1;
      });
    }, 1000);
    ref.current = tick;
    return () => clearInterval(tick);
  }, [interval, onTick]);

  const pct = ((interval - remaining) / interval) * 100;

  return (
    <div className="flex items-center gap-2 text-xs text-fg-muted">
      <div className="relative h-4 w-4">
        <svg viewBox="0 0 16 16" className="h-4 w-4 -rotate-90">
          <circle cx="8" cy="8" r="6" fill="none" stroke="var(--border)" strokeWidth="2" />
          <circle cx="8" cy="8" r="6" fill="none" stroke="var(--accent)" strokeWidth="2"
            strokeDasharray={`${2 * Math.PI * 6}`}
            strokeDashoffset={`${2 * Math.PI * 6 * (1 - pct / 100)}`}
            className="transition-all duration-1000" />
        </svg>
      </div>
      обновление через {remaining}с
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

type Tab = "system" | "nodes" | "hosts" | "inbounds";

export default function AdminRemnaWavePage() {
  const [tab, setTab] = useState<Tab>("nodes");
  const [system, setSystem] = useState<SystemStats | null>(null);
  const [nodes, setNodes] = useState<RwNode[]>([]);
  const [hosts, setHosts] = useState<RwHost[]>([]);
  const [inbounds, setInbounds] = useState<RwInbound[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [totalOnline, setTotalOnline] = useState<number | null>(null);

  const fetchTab = useCallback(async (t: Tab, silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    setErr(null);
    try {
      if (t === "system") {
        const d = await adminApi.get<SystemStats>("/remnawave/system");
        setSystem(d);
        if (d.metadata?.version) setVersion(d.metadata.version);
      } else if (t === "nodes") {
        const d = await adminApi.get<{ nodes: RwNode[] }>("/remnawave/nodes");
        setNodes(d.nodes ?? []);
        const online = (d.nodes ?? []).reduce((s, n) => s + (n.users_online ?? 0), 0);
        setTotalOnline(online);
      } else if (t === "hosts") {
        const d = await adminApi.get<{ hosts: RwHost[] }>("/remnawave/hosts");
        setHosts(d.hosts ?? []);
      } else if (t === "inbounds") {
        const d = await adminApi.get<{ inbounds: RwInbound[] }>("/remnawave/inbounds");
        setInbounds(d.inbounds ?? []);
      }
    } catch (e: any) {
      setErr(e?.detail ?? e?.message ?? "Ошибка соединения с RemnaWave");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchTab(tab); }, [tab, fetchTab]);

  const handleAutoRefresh = useCallback(() => {
    fetchTab(tab, true);
  }, [tab, fetchTab]);

  const handleNodeAction = async (uuid: string, action: "restart" | "enable" | "disable") => {
    await adminApi.post(`/remnawave/nodes/${uuid}/${action}`);
    await fetchTab("nodes", true);
  };

  const s = system?.stats;

  const tabs: { key: Tab; label: string; icon: React.ElementType }[] = [
    { key: "nodes", label: "Ноды", icon: Activity },
    { key: "system", label: "Система", icon: Server },
    { key: "hosts", label: "Хосты", icon: Globe },
    { key: "inbounds", label: "Инбаунды", icon: Layers },
  ];

  const onlineNodes = nodes.filter(n => n.is_connected && !n.is_disabled).length;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-fg">RemnaWave</h1>
          {version && <p className="mt-0.5 text-sm text-fg-muted">v{version}</p>}
        </div>
        <div className="flex items-center gap-3">
          {(tab === "nodes" || tab === "system") && (
            <RefreshTimer interval={30} onTick={handleAutoRefresh} />
          )}
          <button onClick={() => fetchTab(tab)} disabled={loading || refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition-colors disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${(loading || refreshing) ? "animate-spin" : ""}`} />
            Обновить
          </button>
        </div>
      </div>

      {/* Quick online counter */}
      {tab === "nodes" && nodes.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-[var(--border)] bg-bg-raised px-4 py-3">
            <p className="text-xs text-fg-muted">Онлайн сейчас</p>
            <p className="mt-0.5 text-2xl font-bold text-success">{totalOnline ?? 0}</p>
            <p className="text-xs text-fg-subtle">пользователей</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-bg-raised px-4 py-3">
            <p className="text-xs text-fg-muted">Нод онлайн</p>
            <p className="mt-0.5 text-2xl font-bold text-fg">{onlineNodes} / {nodes.length}</p>
            <p className="text-xs text-fg-subtle">активных</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-bg-raised px-4 py-3">
            <p className="text-xs text-fg-muted">Трафик всего</p>
            <p className="mt-0.5 text-xl font-bold text-fg">
              {fmtBytes(nodes.reduce((s, n) => s + (n.traffic_used_bytes ?? 0), 0))}
            </p>
            <p className="text-xs text-fg-subtle">за всё время</p>
          </div>
          <div className="rounded-xl border border-[var(--border)] bg-bg-raised px-4 py-3">
            <p className="text-xs text-fg-muted">Нод отключено</p>
            <p className="mt-0.5 text-2xl font-bold text-fg">{nodes.filter(n => n.is_disabled).length}</p>
            <p className="text-xs text-fg-subtle">вручную</p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-[var(--border)]">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button key={key} onClick={() => setTab(key)}
            className={`mr-5 flex items-center gap-1.5 border-b-2 pb-2.5 pt-1 text-sm font-medium transition-colors ${tab === key ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg"}`}>
            <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
            {label}
            {key === "nodes" && nodes.length > 0 && (
              <span className="ml-1 rounded-full bg-bg-raised px-1.5 py-0.5 text-[10px] text-fg-subtle">{nodes.length}</span>
            )}
          </button>
        ))}
      </div>

      {err && (
        <div className="flex items-center gap-2 rounded-xl border border-danger/15 bg-danger/8 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />{err}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
        </div>
      ) : (
        <>
          {/* ── Nodes ── */}
          {tab === "nodes" && (
            nodes.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">Ноды не найдены</p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-fg-muted">{refreshing && <span className="mr-1 animate-pulse">↺</span>}Данные обновляются каждые 30 сек</p>
                  <button onClick={async () => { await adminApi.post("/remnawave/nodes/restart-all"); await fetchTab("nodes", true); }}
                    className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs text-fg-muted hover:text-fg transition-colors">
                    Рестарт всех нод
                  </button>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {[...nodes].sort((a, b) => (b.users_online ?? 0) - (a.users_online ?? 0)).map(node => (
                    <NodeCard key={node.uuid} node={node} onAction={handleNodeAction} />
                  ))}
                </div>
              </div>
            )
          )}

          {/* ── System ── */}
          {tab === "system" && system && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard icon={Cpu} label="Процессор" value={`${s?.cpu?.cores ?? "—"} ядер`}
                  sub={s?.cpu?.physical_cores ? `${s.cpu.physical_cores} физ.` : undefined} />
                <StatCard icon={MemoryStick} label="Оперативная память"
                  value={s?.memory ? fmtBytes(s.memory.used) : "—"}
                  sub={s?.memory ? `из ${fmtBytes(s.memory.total)} (${Math.round(s.memory.used / s.memory.total * 100)}%)` : undefined} />
                <StatCard icon={Clock} label="Аптайм сервера"
                  value={s?.uptime != null ? fmtUptime(s.uptime) : "—"} />
                <StatCard icon={Activity} label="Нод онлайн"
                  value={String(s?.nodes?.total_online ?? "—")}
                  sub={s?.nodes?.total_bytes_lifetime ? `${fmtBytes(Number(s.nodes.total_bytes_lifetime))} lifetime` : undefined} />
              </div>

              {s?.users && (
                <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-4">
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-fg-subtle">Пользователи RemnaWave</p>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div><p className="text-xs text-fg-muted">Всего</p><p className="mt-0.5 text-2xl font-bold text-fg">{s.users.total_users}</p></div>
                    {s.online_stats && (
                      <>
                        <div><p className="text-xs text-fg-muted">Онлайн сейчас</p><p className="mt-0.5 text-2xl font-bold text-success">{s.online_stats.online_now}</p></div>
                        <div><p className="text-xs text-fg-muted">За 24ч</p><p className="mt-0.5 text-2xl font-bold text-fg">{s.online_stats.last_day}</p></div>
                        <div><p className="text-xs text-fg-muted">За неделю</p><p className="mt-0.5 text-2xl font-bold text-fg">{s.online_stats.last_week}</p></div>
                      </>
                    )}
                  </div>
                  {s.users.status_counts && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {Object.entries(s.users.status_counts).map(([st, count]) => (
                        <span key={st} className="rounded-md border border-[var(--border)] px-2 py-1 text-xs">
                          <span className="text-fg-muted">{st}</span>
                          <span className="ml-1.5 font-semibold text-fg">{count}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ── Hosts ── */}
          {tab === "hosts" && (
            hosts.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">Хосты не найдены</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
                <table className="w-full min-w-[560px] text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] bg-bg-subtle">
                      {["Название", "Адрес", "Порт", "Инбаунд", "Статус"].map(h => (
                        <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {hosts.map((h, i) => (
                      <tr key={h.uuid} className={`border-b border-[var(--border)] hover:bg-bg-subtle transition-colors ${i === hosts.length - 1 ? "border-0" : ""}`}>
                        <td className="px-4 py-3 font-medium text-fg">{h.remark}</td>
                        <td className="px-4 py-3 font-mono text-xs text-fg-muted">{h.address}</td>
                        <td className="px-4 py-3 text-xs text-fg-muted">{h.port}</td>
                        <td className="px-4 py-3 text-xs">
                          {h.inbound?.tag ? (
                            <span className="rounded-md bg-accent/8 px-1.5 py-0.5 text-[10px] font-medium text-accent">{h.inbound.tag}</span>
                          ) : <span className="text-fg-subtle">—</span>}
                        </td>
                        <td className="px-4 py-3">
                          {h.is_disabled
                            ? <span className="text-xs text-fg-subtle">Отключён</span>
                            : <span className="text-xs font-medium text-success">Активен</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}

          {/* ── Inbounds ── */}
          {tab === "inbounds" && (
            inbounds.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">Инбаунды не найдены</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
                <table className="w-full min-w-[560px] text-sm">
                  <thead>
                    <tr className="border-b border-[var(--border)] bg-bg-subtle">
                      {["Тег", "Тип", "Сеть", "Безопасность", "Порт"].map(h => (
                        <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {inbounds.map((ib, i) => (
                      <tr key={ib.uuid} className={`border-b border-[var(--border)] hover:bg-bg-subtle ${i === inbounds.length - 1 ? "border-0" : ""}`}>
                        <td className="px-4 py-3 font-medium text-fg">{ib.tag}</td>
                        <td className="px-4 py-3">
                          <span className="rounded-md bg-accent/8 px-1.5 py-0.5 text-[10px] font-medium text-accent">{ib.type}</span>
                        </td>
                        <td className="px-4 py-3 text-xs text-fg-muted">{ib.network ?? "—"}</td>
                        <td className="px-4 py-3 text-xs text-fg-muted">{ib.security ?? "—"}</td>
                        <td className="px-4 py-3 font-mono text-xs text-fg-muted">{ib.port ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}
