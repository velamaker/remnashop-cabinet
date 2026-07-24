import { useEffect, useState } from "react";
import { Activity, Save, AlertCircle, CheckCircle2, ShieldCheck } from "lucide-react";
import { serverStatusAdminApi, type ServerStatusConfig, type AdminPanelNode } from "@/api/admin";
import { Flag } from "@/components/Flag";

// Тумблер — тот же вид, что в остальных админ-разделах.
function Switch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg ${
        checked ? "bg-accent" : "bg-border"
      }`}
    >
      <span
        className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
          checked ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function Row({
  label,
  sub,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  sub?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div
      className={`flex items-center justify-between gap-4 rounded-xl bg-bg px-4 py-3 ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{label}</p>
        {sub && <p className="mt-0.5 text-xs text-fg-muted">{sub}</p>}
      </div>
      <Switch checked={checked} onChange={(v) => !disabled && onChange(v)} />
    </div>
  );
}

// Раздел «Статус сервиса» — управление блоком серверов в кабинете пользователя.
export default function AdminServerStatusPage() {
  const [cfg, setCfg] = useState<ServerStatusConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<AdminPanelNode[] | null>(null);
  // Явный режим «выбирать вручную» — НЕ выводим из visible_nodes.length, иначе при
  // невыгруженном списке нод тумблер отскакивал назад (visible_nodes=[]=«все»).
  const [manual, setManual] = useState(false);

  useEffect(() => {
    serverStatusAdminApi
      .get()
      .then((c) => {
        setCfg(c);
        setManual((c.visible_nodes?.length ?? 0) > 0);
      })
      .catch(() => setError("Не удалось загрузить"))
      .finally(() => setLoading(false));
    serverStatusAdminApi
      .nodes()
      .then((r) => setNodes(r.nodes))
      .catch(() => setNodes([]));
  }, []);

  const showAllNodes = !manual;

  const setShowAllNodes = (all: boolean) => {
    setManual(!all);
    if (all) patch({ visible_nodes: [] }); // все (в т.ч. будущие)
    // при переходе в ручной режим оставляем текущий выбор как есть (можно пустой)
  };

  const toggleNode = (uuid: string, on: boolean) => {
    if (!cfg) return;
    const cur = new Set(cfg.visible_nodes);
    if (on) cur.add(uuid);
    else cur.delete(uuid);
    const order = (nodes ?? []).map((n) => n.uuid);
    // сохраняем в порядке списка нод; неизвестные (нет в списке) — в конец
    const ordered = order.filter((u) => cur.has(u));
    for (const u of cur) if (!order.includes(u)) ordered.push(u);
    patch({ visible_nodes: ordered });
  };

  const patch = (p: Partial<ServerStatusConfig>) => {
    setCfg((c) => (c ? { ...c, ...p } : c));
    setSaved(false);
  };

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const next = await serverStatusAdminApi.update(cfg);
      setCfg(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      setError("Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 px-1 pt-1">
        <Activity className="h-[18px] w-[18px] text-accent" />
        <h1 className="text-lg font-bold text-fg md:text-xl">Статус сервиса</h1>
      </div>

      <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <div className="mb-4">
          <h3 className="text-sm font-semibold text-fg">Блок серверов в кабинете</h3>
          <p className="mt-0.5 text-xs text-fg-muted">
            Список серверов со статусом онлайн/офлайн на главной кабинета и на публичной
            странице статуса.
          </p>
        </div>

        {loading ? (
          <p className="text-sm text-fg-muted">Загрузка…</p>
        ) : !cfg ? (
          <p className="text-sm text-danger">{error ?? "Ошибка"}</p>
        ) : (
          <div className="space-y-2.5">
            <Row
              label="Показывать блок статуса"
              sub="Общий переключатель. Выключено — блок скрыт везде, публичный статус ничего не отдаёт."
              checked={cfg.enabled}
              onChange={(v) => patch({ enabled: v })}
            />
            <Row
              label="Привязка по подписке"
              sub="Вошедший пользователь видит только серверы своей подписки (свои сквады), а не все ноды панели."
              checked={cfg.bind_to_subscription}
              disabled={!cfg.enabled}
              onChange={(v) => patch({ bind_to_subscription: v })}
            />
            <Row
              label="Показывать невошедшим"
              sub="Блок на публичной странице статуса (без входа). Адреса серверов там не раскрываются и пинг не меряется."
              checked={cfg.guest_visible}
              disabled={!cfg.enabled}
              onChange={(v) => patch({ guest_visible: v })}
            />

            {/* Какие ноды показывать */}
            <div className={`rounded-xl bg-bg px-4 py-3 ${!cfg.enabled ? "opacity-50" : ""}`}>
              <Row
                label="Показывать все серверы"
                sub="Выключите, чтобы выбрать вручную, какие ноды видны в статусе (остальные скрыты)."
                checked={showAllNodes}
                disabled={!cfg.enabled}
                onChange={(v) => setShowAllNodes(v)}
              />

              {!showAllNodes && (
                <div className="mt-3 border-t border-border-subtle pt-3">
                  {nodes === null ? (
                    <p className="text-sm text-fg-muted">Загрузка серверов…</p>
                  ) : nodes.length === 0 ? (
                    <p className="text-sm text-fg-muted">Ноды не найдены (панель недоступна?).</p>
                  ) : (
                    <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                      {nodes.map((n) => {
                        const on = cfg.visible_nodes.includes(n.uuid);
                        return (
                          <label
                            key={n.uuid}
                            className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm text-fg hover:bg-bg-subtle"
                          >
                            <input
                              type="checkbox"
                              checked={on}
                              disabled={!cfg.enabled}
                              onChange={(e) => toggleNode(n.uuid, e.target.checked)}
                              className="h-4 w-4 accent-[var(--accent)]"
                            />
                            {n.country_code && <Flag code={n.country_code} className="h-3.5 w-5" />}
                            <span className="min-w-0 flex-1 truncate">{n.name || n.uuid}</span>
                            <span
                              className={`h-2 w-2 shrink-0 rounded-full ${
                                n.disabled ? "bg-border" : n.online ? "bg-success" : "bg-danger"
                              }`}
                              title={n.disabled ? "Отключена в панели" : n.online ? "Онлайн" : "Офлайн"}
                            />
                          </label>
                        );
                      })}
                    </div>
                  )}
                  <p className="mt-2 text-xs text-fg-subtle">
                    Отмеченные серверы видны в статусе. Отключённые в панели ноды не показываются в любом случае.
                  </p>
                </div>
              )}
            </div>

            {/* Сервисные хосты-заглушки */}
            <div className={`rounded-xl bg-bg px-4 py-3 ${!cfg.enabled ? "opacity-50" : ""}`}>
              <p className="text-sm font-medium text-fg">Сервисные хосты (заглушки)</p>
              <p className="mt-0.5 text-xs text-fg-muted">
                Слова из названий хостов-заглушек (через запятую): «ПОДПИСКА, Продлите, Оплата, Резерв,
                Автовыбор». Такие хосты скрыты у активных подписчиков и показываются только тем, у кого
                подписка кончилась. Пусто — ничего не прячем.
              </p>
              <input
                value={(cfg.service_keywords ?? []).join(", ")}
                disabled={!cfg.enabled}
                onChange={(e) =>
                  patch({
                    service_keywords: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="ПОДПИСКА, Продлите, Оплата, Резерв, Автовыбор"
                className="mt-2 w-full rounded-lg border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg outline-none focus:border-accent"
              />
            </div>

            <div className="flex items-start gap-2 rounded-xl border border-success/20 bg-success/5 px-4 py-3 text-xs text-fg-muted">
              <ShieldCheck className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
              <span>
                Приватность: адрес (host) сервера уходит в браузер только вошедшему владельцу —
                для замера пинга. На публичном статусе адресов нет, поэтому IP серверов не
                утекает.
              </span>
            </div>

            {error && (
              <div className="flex items-center gap-2 text-sm text-danger">
                <AlertCircle className="h-4 w-4" /> {error}
              </div>
            )}

            <div className="flex justify-end pt-1">
              <button
                type="button"
                onClick={save}
                disabled={saving}
                className="inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-accent/90 disabled:opacity-60"
              >
                {saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}
                {saved ? "Сохранено" : saving ? "Сохранение…" : "Сохранить"}
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
