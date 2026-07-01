import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, ToggleLeft, ToggleRight, Edit2, AlertCircle, X, ChevronDown, ChevronUp } from "lucide-react";
import { plansAdminApi, type AdminPlan, type AdminPlanDuration } from "@/api/admin";
import { ApiError } from "@/types/api";

const PLAN_TYPES = ["BOTH", "TRAFFIC", "DEVICES"];
const AVAILABILITIES = ["ALL", "NEW", "EXISTING"];
const CURRENCIES = ["RUB", "XTR", "USD"];
const TRAFFIC_STRATEGIES = ["NO_RESET", "MONTHLY_RESET", "WEEKLY_RESET", "DAILY_RESET"];

// В тарифах traffic_limit хранится в ГБ (не в байтах) — конвертация не нужна.
function formatTraffic(gb: number): string {
  if (gb === 0) return "∞";
  return `${gb} ГБ`;
}

function PlanModal({
  plan,
  onClose,
  onSaved,
}: {
  plan: AdminPlan | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = plan !== null;
  const [name, setName] = useState(plan?.name ?? "");
  const [description, setDescription] = useState(plan?.description ?? "");
  const [tag, setTag] = useState(plan?.tag ?? "");
  const [publicCode, setPublicCode] = useState(plan?.public_code ?? "");
  const [type, setType] = useState(plan?.type ?? "BOTH");
  const [availability, setAvailability] = useState(plan?.availability ?? "ALL");
  const [trafficLimit, setTrafficLimit] = useState(String(plan?.traffic_limit ?? 0));
  const [deviceLimit, setDeviceLimit] = useState(String(plan?.device_limit ?? 1));
  const [trafficStrategy, setTrafficStrategy] = useState(plan?.traffic_limit_strategy ?? "NO_RESET");
  const [isActive, setIsActive] = useState(plan?.is_active ?? false);
  const [isTrial, setIsTrial] = useState(plan?.is_trial ?? false);
  const [durations, setDurations] = useState<AdminPlanDuration[]>(plan?.durations ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addDuration = () => {
    setDurations([...durations, { days: 30, order_index: durations.length, prices: [{ currency: "RUB", price: "0" }] }]);
  };

  const removeDuration = (i: number) => {
    setDurations(durations.filter((_, idx) => idx !== i));
  };

  const updateDuration = (i: number, field: "days", value: number) => {
    setDurations(durations.map((d, idx) => idx === i ? { ...d, [field]: value } : d));
  };

  const updatePrice = (di: number, pi: number, field: "currency" | "price", value: string) => {
    setDurations(durations.map((d, idx) => {
      if (idx !== di) return d;
      return { ...d, prices: d.prices.map((p, pidx) => pidx === pi ? { ...p, [field]: value } : p) };
    }));
  };

  const addPrice = (di: number) => {
    setDurations(durations.map((d, idx) =>
      idx === di ? { ...d, prices: [...d.prices, { currency: "XTR", price: "0" }] } : d
    ));
  };

  const removePrice = (di: number, pi: number) => {
    setDurations(durations.map((d, idx) =>
      idx === di ? { ...d, prices: d.prices.filter((_, pidx) => pidx !== pi) } : d
    ));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const payload = {
      name, description: description || null, tag: tag || null, public_code: publicCode || null,
      type, availability, traffic_limit_strategy: trafficStrategy,
      traffic_limit: Number(trafficLimit), device_limit: Number(deviceLimit),
      is_active: isActive, is_trial: isTrial, durations,
    };
    try {
      if (isEdit) await plansAdminApi.update(plan!.id, payload);
      else await plansAdminApi.create(payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-10 backdrop-blur-sm overflow-y-auto">
      <div className="w-full max-w-2xl rounded-2xl border border-border-subtle bg-bg shadow-xl mb-10">
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <h2 className="text-base font-semibold text-fg">{isEdit ? "Редактировать тариф" : "Новый тариф"}</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg"><X className="h-5 w-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-5 px-6 py-5">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Название *</label>
              <input value={name} onChange={e => setName(e.target.value)} required className="input w-full" placeholder="Базовый" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Публичный код</label>
              <input value={publicCode} onChange={e => setPublicCode(e.target.value)} className="input w-full" placeholder="basic" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-fg-muted">Описание</label>
            <input value={description} onChange={e => setDescription(e.target.value)} className="input w-full" placeholder="..." />
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Тип</label>
              <select value={type} onChange={e => setType(e.target.value)} className="input w-full">
                {PLAN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Доступность</label>
              <select value={availability} onChange={e => setAvailability(e.target.value)} className="input w-full">
                {AVAILABILITIES.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Сброс трафика</label>
              <select value={trafficStrategy} onChange={e => setTrafficStrategy(e.target.value)} className="input w-full">
                {TRAFFIC_STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Трафик (ГБ, 0 = ∞)</label>
              <input type="number" min={0} value={trafficLimit} onChange={e => setTrafficLimit(e.target.value)} className="input w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">Устройств</label>
              <input type="number" min={0} value={deviceLimit} onChange={e => setDeviceLimit(e.target.value)} className="input w-full" />
            </div>
          </div>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={isActive} onChange={e => setIsActive(e.target.checked)} className="h-4 w-4 accent-accent" />
              <span className="text-sm text-fg">Активен</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={isTrial} onChange={e => setIsTrial(e.target.checked)} className="h-4 w-4 accent-accent" />
              <span className="text-sm text-fg">Пробный</span>
            </label>
          </div>

          {/* Durations */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-semibold text-fg">Длительности и цены</p>
              <button type="button" onClick={addDuration} className="flex items-center gap-1 text-xs text-accent hover:underline">
                <Plus className="h-3 w-3" /> Добавить
              </button>
            </div>
            <div className="space-y-3">
              {durations.map((d, di) => (
                <div key={di} className="rounded-xl border border-border-subtle p-4">
                  <div className="mb-3 flex items-center gap-3">
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-fg-muted">Дней</label>
                      <input type="number" min={1} value={d.days} onChange={e => updateDuration(di, "days", Number(e.target.value))} className="input w-full" />
                    </div>
                    <button type="button" onClick={() => removeDuration(di)} className="mt-5 text-fg-muted hover:text-danger">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="space-y-2">
                    {d.prices.map((p, pi) => (
                      <div key={pi} className="flex items-center gap-2">
                        <select value={p.currency} onChange={e => updatePrice(di, pi, "currency", e.target.value)} className="input w-24 flex-shrink-0">
                          {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                        <input type="number" min={0} value={p.price} onChange={e => updatePrice(di, pi, "price", e.target.value)} className="input flex-1" placeholder="0" />
                        <button type="button" onClick={() => removePrice(di, pi)} className="text-fg-muted hover:text-danger">
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                    <button type="button" onClick={() => addPrice(di)} className="text-xs text-accent hover:underline">
                      + валюта
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {error && <p className="rounded-xl bg-danger/10 px-4 py-2 text-sm text-danger">{error}</p>}
          <div className="flex gap-3">
            <button type="button" onClick={onClose} className="flex-1 rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition-colors">Отмена</button>
            <button type="submit" disabled={saving} className="flex-1 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50 transition-colors">
              {saving ? "Сохранение…" : isEdit ? "Сохранить" : "Создать"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function AdminPlansPage() {
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalPlan, setModalPlan] = useState<AdminPlan | null | false>(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    plansAdminApi.list()
      .then(r => setPlans(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (plan: AdminPlan) => {
    try {
      await plansAdminApi.toggle(plan.id);
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : "Ошибка"); }
  };

  const remove = async (plan: AdminPlan) => {
    if (!confirm(`Удалить тариф «${plan.name}»?`)) return;
    try {
      await plansAdminApi.delete(plan.id);
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : "Ошибка"); }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">Тарифы</h1>
        <button onClick={() => setModalPlan(null)} className="flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 transition-colors">
          <Plus className="h-4 w-4" /> Создать
        </button>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : plans.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">Тарифов нет</div>
      ) : (
        <div className="space-y-3">
          {plans.map(plan => (
            <div key={plan.id} className="rounded-2xl border border-border-subtle bg-bg-subtle overflow-hidden">
              <div className="flex items-center gap-4 px-5 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-fg">{plan.name}</span>
                    {plan.is_trial && <span className="rounded-full bg-warning/10 px-2 py-0.5 text-xs text-warning">Пробный</span>}
                    {plan.is_active ? (
                      <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs text-success">Активен</span>
                    ) : (
                      <span className="rounded-full bg-fg-subtle/20 px-2 py-0.5 text-xs text-fg-muted">Выключен</span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    {formatTraffic(plan.traffic_limit)} · {plan.device_limit === 0 ? "∞" : plan.device_limit} уст. · {plan.type} · {plan.durations.length} вариантов
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => toggle(plan)}
                    className={`rounded-lg p-1.5 transition-colors ${
                      plan.is_active ? "text-success hover:text-success" : "text-fg-muted hover:text-fg"
                    }`}
                    title={plan.is_active ? "Включён — нажмите, чтобы выключить" : "Выключен — нажмите, чтобы включить"}
                  >
                    {plan.is_active ? <ToggleRight className="h-5 w-5" /> : <ToggleLeft className="h-5 w-5" />}
                  </button>
                  <button onClick={() => setModalPlan(plan)} className="rounded-lg p-1.5 text-fg-muted hover:text-accent transition-colors">
                    <Edit2 className="h-4 w-4" />
                  </button>
                  <button onClick={() => remove(plan)} className="rounded-lg p-1.5 text-fg-muted hover:text-danger transition-colors">
                    <Trash2 className="h-4 w-4" />
                  </button>
                  <button onClick={() => setExpanded(expanded === plan.id ? null : plan.id)} className="rounded-lg p-1.5 text-fg-muted hover:text-fg transition-colors">
                    {expanded === plan.id ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                  </button>
                </div>
              </div>
              {expanded === plan.id && plan.durations.length > 0 && (
                <div className="border-t border-border-subtle px-5 py-3">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                    {plan.durations.map((d, i) => (
                      <div key={i} className="rounded-xl bg-bg-raised p-3 text-xs">
                        <p className="font-semibold text-fg mb-1">{d.days} дней</p>
                        {d.prices.map((p, pi) => (
                          <p key={pi} className="text-fg-muted">{p.price} {p.currency}</p>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {modalPlan !== false && (
        <PlanModal plan={modalPlan} onClose={() => setModalPlan(false)} onSaved={load} />
      )}
    </div>
  );
}
