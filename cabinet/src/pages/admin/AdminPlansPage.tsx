import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, ToggleLeft, ToggleRight, Edit2, AlertCircle, X, ChevronDown, ChevronUp } from "lucide-react";
import { plansAdminApi, type AdminPlan, type AdminPlanDuration, type AdminSquadsResponse } from "@/api/admin";
import { ApiError } from "@/types/api";
import { useI18n, useT } from "@/i18n/I18nContext";
import { pluralFor } from "@/lib/pluralRu";
import { translate } from "@/i18n/translate";

const PLAN_TYPES: { value: string; labelKey: string }[] = [
  { value: "BOTH", labelKey: "adm.plans.type_both" },
  { value: "TRAFFIC", labelKey: "adm.plans.type_traffic" },
  { value: "DEVICES", labelKey: "adm.plans.type_devices" },
];
const AVAILABILITIES: { value: string; labelKey: string }[] = [
  { value: "ALL", labelKey: "adm.plans.avail_all" },
  { value: "NEW", labelKey: "adm.plans.avail_new" },
  { value: "EXISTING", labelKey: "adm.plans.avail_existing" },
  { value: "INVITED", labelKey: "adm.plans.avail_invited" },
  { value: "ALLOWED", labelKey: "adm.plans.avail_allowed" },
  { value: "LINK", labelKey: "adm.plans.avail_link" },
];
const CURRENCIES = ["RUB", "XTR", "USD"];
const TRAFFIC_STRATEGIES: { value: string; labelKey: string }[] = [
  { value: "NO_RESET", labelKey: "adm.plans.reset_none" },
  { value: "MONTHLY_RESET", labelKey: "adm.plans.reset_monthly" },
  { value: "WEEKLY_RESET", labelKey: "adm.plans.reset_weekly" },
  { value: "DAILY_RESET", labelKey: "adm.plans.reset_daily" },
];

// В тарифах traffic_limit хранится в ГБ (не в байтах) — конвертация не нужна.
function formatTraffic(gb: number): string {
  if (gb === 0) return "∞";
  return translate("adm.plans.gb", { n: gb });
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
  const t = useT();
  const { lang } = useI18n();
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
  // Разрешённые пользователи (по одному в строке) — как в боте: tg-id и email раздельно
  const [allowedTgText, setAllowedTgText] = useState((plan?.allowed_telegram_ids ?? []).join("\n"));
  const [allowedEmailText, setAllowedEmailText] = useState((plan?.allowed_emails ?? []).join("\n"));
  // Сквады Remnawave
  const [internalSquads, setInternalSquads] = useState<string[]>(plan?.internal_squads ?? []);
  const [externalSquad, setExternalSquad] = useState<string>(plan?.external_squad ?? "");
  const [squads, setSquads] = useState<AdminSquadsResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    plansAdminApi.squads().then(setSquads).catch(() => setSquads(null));
  }, []);

  const toggleInternalSquad = (uuid: string) => {
    setInternalSquads((prev) => (prev.includes(uuid) ? prev.filter((x) => x !== uuid) : [...prev, uuid]));
  };

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
    const allowed_telegram_ids = allowedTgText
      .split(/[\s,]+/)
      .map((x) => Number(x.trim()))
      .filter((n) => Number.isInteger(n) && n > 0);
    const allowed_emails = allowedEmailText
      .split(/[\s,]+/)
      .map((x) => x.trim())
      .filter((x) => x.length > 0);
    const payload = {
      name, description: description || null, tag: tag || null, public_code: publicCode || null,
      type, availability, traffic_limit_strategy: trafficStrategy,
      traffic_limit: Number(trafficLimit), device_limit: Number(deviceLimit),
      is_active: isActive, is_trial: isTrial, durations,
      allowed_telegram_ids, allowed_emails,
      internal_squads: internalSquads,
      external_squad: externalSquad || null,
      clear_external_squad: !externalSquad,
    };
    try {
      if (isEdit) await plansAdminApi.update(plan!.id, payload);
      else await plansAdminApi.create(payload);
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.plans.save_error"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-10 backdrop-blur-sm overflow-y-auto">
      <div className="w-full max-w-2xl rounded-2xl border border-border-subtle bg-bg shadow-xl mb-10">
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-4">
          <h2 className="text-base font-semibold text-fg">{isEdit ? t("adm.plans.modal_edit") : t("adm.plans.modal_new")}</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-fg-muted hover:text-fg"><X className="h-5 w-5" /></button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-5 px-6 py-5">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.name")}</label>
              <input value={name} onChange={e => setName(e.target.value)} required className="input w-full" placeholder={t("adm.plans.name_ph")} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.public_code")}</label>
              <input value={publicCode} onChange={e => setPublicCode(e.target.value)} className="input w-full" placeholder="basic" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.description")}</label>
              <input value={description} onChange={e => setDescription(e.target.value)} className="input w-full" placeholder="..." />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.tag")}</label>
              <input value={tag} onChange={e => setTag(e.target.value)} className="input w-full" placeholder={t("adm.plans.tag_ph")} />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.type")}</label>
              <select value={type} onChange={e => setType(e.target.value)} className="input w-full">
                {PLAN_TYPES.map(pt => <option key={pt.value} value={pt.value}>{t(pt.labelKey)}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.availability")}</label>
              <select value={availability} onChange={e => setAvailability(e.target.value)} className="input w-full">
                {AVAILABILITIES.map(a => <option key={a.value} value={a.value}>{t(a.labelKey)}</option>)}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.traffic_reset")}</label>
              <select value={trafficStrategy} onChange={e => setTrafficStrategy(e.target.value)} className="input w-full">
                {TRAFFIC_STRATEGIES.map(s => <option key={s.value} value={s.value}>{t(s.labelKey)}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.traffic_field")}</label>
              <input type="number" min={0} value={trafficLimit} onChange={e => setTrafficLimit(e.target.value)} className="input w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.devices_field")}</label>
              <input type="number" min={0} value={deviceLimit} onChange={e => setDeviceLimit(e.target.value)} className="input w-full" />
            </div>
          </div>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={isActive} onChange={e => setIsActive(e.target.checked)} className="h-4 w-4 accent-accent" />
              <span className="text-sm text-fg">{t("adm.plans.active")}</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={isTrial} onChange={e => setIsTrial(e.target.checked)} className="h-4 w-4 accent-accent" />
              <span className="text-sm text-fg">{t("adm.plans.trial")}</span>
            </label>
          </div>

          {/* Разрешённые пользователи — только для доступности «Для разрешённых» */}
          {availability === "ALLOWED" && (
            <div className="rounded-xl border border-border-subtle p-4">
              <p className="mb-1 text-sm font-semibold text-fg">{t("adm.plans.allowed_title")}</p>
              <p className="mb-3 text-xs text-fg-muted">{t("adm.plans.allowed_hint")}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium text-fg-muted">Telegram ID</label>
                  <textarea value={allowedTgText} onChange={e => setAllowedTgText(e.target.value)} rows={3} className="input w-full font-mono-tech text-xs" placeholder="123456789&#10;987654321" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-fg-muted">Email</label>
                  <textarea value={allowedEmailText} onChange={e => setAllowedEmailText(e.target.value)} rows={3} className="input w-full font-mono-tech text-xs" placeholder="user@mail.com" />
                </div>
              </div>
            </div>
          )}

          {/* Сквады Remnawave */}
          {squads && (squads.internal.length > 0 || squads.external.length > 0) && (
            <div className="rounded-xl border border-border-subtle p-4">
              <p className="mb-1 text-sm font-semibold text-fg">{t("adm.plans.squads_title")}</p>
              <p className="mb-3 text-xs text-fg-muted">{t("adm.plans.squads_hint")}</p>
              {squads.internal.length > 0 && (
                <div className="mb-4">
                  <label className="mb-1.5 block text-xs font-medium text-fg-muted">{t("adm.plans.squads_internal")}</label>
                  <div className="flex flex-wrap gap-2">
                    {squads.internal.map((s) => {
                      const on = internalSquads.includes(s.uuid);
                      return (
                        <button
                          key={s.uuid}
                          type="button"
                          onClick={() => toggleInternalSquad(s.uuid)}
                          className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                            on ? "border-accent bg-accent-subtle text-accent" : "border-border-subtle text-fg-muted hover:text-fg"
                          }`}
                        >
                          {s.name}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              {squads.external.length > 0 && (
                <div>
                  <label className="mb-1 block text-xs font-medium text-fg-muted">{t("adm.plans.squads_external")}</label>
                  <select value={externalSquad} onChange={e => setExternalSquad(e.target.value)} className="input w-full">
                    <option value="">{t("adm.plans.squad_none")}</option>
                    {squads.external.map((s) => <option key={s.uuid} value={s.uuid}>{s.name}</option>)}
                  </select>
                </div>
              )}
            </div>
          )}

          {/* Durations */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-semibold text-fg">{t("adm.plans.durations_title")}</p>
              <button type="button" onClick={addDuration} className="flex items-center gap-1 text-xs text-accent hover:underline">
                <Plus className="h-3 w-3" /> {t("adm.plans.add")}
              </button>
            </div>
            <div className="space-y-3">
              {durations.map((d, di) => (
                <div key={di} className="rounded-xl border border-border-subtle p-4">
                  <div className="mb-3 flex items-center gap-3">
                    <div className="flex-1">
                      <label className="mb-1 block text-xs text-fg-muted">{t("adm.plans.days_field")}</label>
                      <input type="number" min={1} value={d.days} onChange={e => updateDuration(di, "days", Number(e.target.value))} className="input w-full" />
                    </div>
                    <button type="button" onClick={() => removeDuration(di)} className="mt-5 text-fg-muted hover:text-danger">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="space-y-2">
                    {d.prices.map((p, pi) => (
                      <div key={pi} className="flex items-center gap-2">
                        <select value={p.currency} onChange={e => updatePrice(di, pi, "currency", e.target.value)} className="input flex-shrink-0" style={{ width: "5.5rem" }}>
                          {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                        <input type="number" min={0} value={p.price} onChange={e => updatePrice(di, pi, "price", e.target.value)} className="input min-w-0 flex-1" placeholder={t("adm.plans.price_ph")} />
                        <button type="button" onClick={() => removePrice(di, pi)} className="text-fg-muted hover:text-danger">
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                    <button type="button" onClick={() => addPrice(di)} className="text-xs text-accent hover:underline">
                      {t("adm.plans.add_currency")}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {error && <p className="rounded-xl bg-danger/10 px-4 py-2 text-sm text-danger">{error}</p>}
          <div className="flex gap-3">
            <button type="button" onClick={onClose} className="flex-1 rounded-xl border border-border-subtle px-4 py-2.5 text-sm text-fg-muted hover:text-fg transition-colors">{t("adm.plans.cancel")}</button>
            <button type="submit" disabled={saving} className="flex-1 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-50 transition-colors">
              {saving ? t("adm.plans.saving") : isEdit ? t("adm.plans.save") : t("adm.plans.create")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function AdminPlansPage() {
  const t = useT();
  const { lang } = useI18n();
  const [plans, setPlans] = useState<AdminPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalPlan, setModalPlan] = useState<AdminPlan | null | false>(false);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    plansAdminApi.list()
      .then(r => setPlans(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : translate("adm.plans.error")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (plan: AdminPlan) => {
    try {
      await plansAdminApi.toggle(plan.id);
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : t("adm.plans.error")); }
  };

  const remove = async (plan: AdminPlan) => {
    if (!confirm(t("adm.plans.confirm_delete", { name: plan.name }))) return;
    try {
      await plansAdminApi.delete(plan.id);
      load();
    } catch (e) { alert(e instanceof ApiError ? e.detail : t("adm.plans.error")); }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">{t("adm.plans.title")}</h1>
        <button onClick={() => setModalPlan(null)} className="flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-fg hover:bg-accent/90 transition-colors">
          <Plus className="h-4 w-4" /> {t("adm.plans.create")}
        </button>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : plans.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.plans.empty")}</div>
      ) : (
        <div className="space-y-3">
          {plans.map(plan => (
            <div key={plan.id} className="rounded-2xl border border-border-subtle bg-bg-subtle overflow-hidden">
              <div className="flex items-center gap-4 px-5 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-fg">{plan.name}</span>
                    {plan.is_trial && <span className="rounded-full bg-warning/10 px-2 py-0.5 text-xs text-warning">{t("adm.plans.trial")}</span>}
                    {plan.is_active ? (
                      <span className="rounded-full bg-success/10 px-2 py-0.5 text-xs text-success">{t("adm.plans.active")}</span>
                    ) : (
                      <span className="rounded-full bg-fg-subtle/20 px-2 py-0.5 text-xs text-fg-muted">{t("adm.plans.disabled")}</span>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    {t("adm.plans.summary", {
                      traffic: formatTraffic(plan.traffic_limit),
                      devices: plan.device_limit === 0 ? "∞" : plan.device_limit,
                      // Счётные слова — отдельными ключами: по-английски «1 devices»
                      // и «1 options» читаются как ошибка вёрстки.
                      devicesWord: t(
                        pluralFor(lang, plan.device_limit, "adm.plans.dev_one", "adm.plans.dev_few", "adm.plans.dev_many"),
                      ),
                      type: plan.type,
                      n: plan.durations.length,
                      optionsWord: t(
                        pluralFor(lang, plan.durations.length, "adm.plans.opt_one", "adm.plans.opt_few", "adm.plans.opt_many"),
                      ),
                    })}
                  </p>
                </div>
                <div className="flex items-center gap-2.5">
                  <button
                    onClick={() => toggle(plan)}
                    className={`rounded-lg p-2 transition-colors ${
                      plan.is_active ? "text-success hover:text-success" : "text-fg-muted hover:text-fg"
                    }`}
                    title={plan.is_active ? t("adm.plans.toggle_on_hint") : t("adm.plans.toggle_off_hint")}
                  >
                    {plan.is_active ? <ToggleRight className="h-8 w-8" /> : <ToggleLeft className="h-8 w-8" />}
                  </button>
                  <button onClick={() => setModalPlan(plan)} className="rounded-lg p-2 text-fg-muted hover:text-accent transition-colors" title={t("adm.plans.edit")}>
                    <Edit2 className="h-6 w-6" />
                  </button>
                  <button onClick={() => remove(plan)} className="rounded-lg p-2 text-fg-muted hover:text-danger transition-colors" title={t("adm.plans.delete")}>
                    <Trash2 className="h-6 w-6" />
                  </button>
                  <button onClick={() => setExpanded(expanded === plan.id ? null : plan.id)} className="rounded-lg p-2 text-fg-muted hover:text-fg transition-colors" title={t("adm.plans.details")}>
                    {expanded === plan.id ? <ChevronUp className="h-6 w-6" /> : <ChevronDown className="h-6 w-6" />}
                  </button>
                </div>
              </div>
              {expanded === plan.id && plan.durations.length > 0 && (
                <div className="border-t border-border-subtle px-5 py-3">
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                    {plan.durations.map((d, i) => (
                      <div key={i} className="rounded-xl bg-bg-raised p-3 text-xs">
                        <p className="font-semibold text-fg mb-1">{t("adm.plans.days_n", { n: d.days })}</p>
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
