import { useEffect, useState } from "react";
import { Gift, Save, AlertCircle, CheckCircle2, Coins, Plus, Trash2 } from "lucide-react";
import { settingsAdminApi, cashbackAdminApi, type AdminSettings, type CashbackConfig } from "@/api/admin";
import { ApiError } from "@/types/api";

function Field({ label, value, onChange, type = "number" }: { label: string; value: string; onChange: (v: string) => void; type?: string }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-fg-muted">{label}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent" />
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-fg-muted">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

export default function AdminReferralPage() {
  const [s, setS] = useState<AdminSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    settingsAdminApi.get().then(setS).catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка")).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="flex min-h-64 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>;
  }
  if (!s) {
    return <div className="flex flex-col items-center gap-3 py-20 text-center"><AlertCircle className="h-10 w-10 text-danger" /><p className="text-fg-muted">{error ?? "Ошибка"}</p></div>;
  }

  const r = s.referral;
  const cfg = r.reward.config as Record<string, number>;
  const l1 = Number(cfg["1"] ?? cfg["FIRST"] ?? 0);
  const l2 = Number(cfg["2"] ?? cfg["SECOND"] ?? 0);
  const isDays = r.reward.type === "EXTRA_DAYS";
  const unit = r.reward.strategy === "PERCENT" ? "%" : isDays ? "дней" : "баллов";
  const twoLevels = Number(r.level) >= 2;

  const setRef = (patch: Partial<AdminSettings["referral"]>) => setS((p) => (p ? { ...p, referral: { ...p.referral, ...patch } } : p));
  const setReward = (patch: Partial<AdminSettings["referral"]["reward"]>) => setRef({ reward: { ...r.reward, ...patch } });
  const setCfg = (a: number, b: number) => setReward({ config: { "1": a, "2": b } });

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await settingsAdminApi.update({
        referral: {
          enable: r.enable,
          level: Number(r.level) || 1,
          reward_type: r.reward.type,
          reward_strategy: r.reward.strategy,
          reward_l1: l1,
          reward_l2: l2,
          accrual_strategy: r.accrual_strategy,
        },
      });
      setS(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-fg"><Gift className="h-6 w-6 text-accent" /> Реферальная программа</h1>
          <p className="mt-1 text-sm text-fg-muted">Награда за приглашённых. Тип «Баллы»: 1 балл = 7 ₽, юзер меняет баллы на баланс в кабинете.</p>
        </div>
        <button onClick={save} disabled={saving}
          className="btn-gradient inline-flex items-center gap-2 rounded-xl border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}
      {saved && <div className="flex items-center gap-2 rounded-xl bg-success/10 px-4 py-3 text-sm text-success"><CheckCircle2 className="h-4 w-4" />Сохранено</div>}

      <div className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
        <button type="button" onClick={() => setRef({ enable: !r.enable })}
          className={`flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${r.enable ? "border-accent/30 bg-accent/5" : "border-border-subtle bg-bg"}`}>
          <div>
            <p className="text-sm font-medium text-fg">Реферальная программа</p>
            <p className="mt-0.5 text-xs text-fg-muted">Включить начисление наград за приглашённых</p>
          </div>
          <span className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${r.enable ? "bg-accent" : "bg-border"}`}>
            <span className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${r.enable ? "translate-x-[22px]" : "translate-x-0.5"}`} />
          </span>
        </button>

        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Тип награды" value={r.reward.type} onChange={(v) => setReward({ type: v })}
            options={[{ value: "POINTS", label: "Баллы (1 балл = 7 ₽)" }, { value: "EXTRA_DAYS", label: "Дни подписки" }]} />
          <Select label="Как считать" value={r.reward.strategy} onChange={(v) => setReward({ strategy: v })}
            options={[{ value: "PERCENT", label: "% от суммы платежа" }, { value: "AMOUNT", label: "Фиксировано" }]} />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={`1-й уровень (${unit})`} value={String(l1)} onChange={(v) => setCfg(Number(v), l2)} />
          {twoLevels && <Field label={`2-й уровень (${unit})`} value={String(l2)} onChange={(v) => setCfg(l1, Number(v))} />}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Уровни" value={String(Number(r.level) || 1)} onChange={(v) => setRef({ level: v })}
            options={[{ value: "1", label: "Только прямые (1 уровень)" }, { value: "2", label: "Два уровня" }]} />
          <Select label="Когда начислять" value={r.accrual_strategy} onChange={(v) => setRef({ accrual_strategy: v })}
            options={[{ value: "ON_FIRST_PAYMENT", label: "За первый платёж реферала" }, { value: "ON_EACH_PAYMENT", label: "За каждый платёж реферала" }]} />
        </div>
      </div>

      <CashbackCard />
    </div>
  );
}

function CashbackCard() {
  const [cfg, setCfg] = useState<CashbackConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    cashbackAdminApi.get().then(setCfg).catch(() => setError("Не удалось загрузить кэшбэк")).finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<CashbackConfig>) => setCfg((c) => (c ? { ...c, ...p } : c));
  const setTier = (i: number, field: "min_days" | "percent", v: number) =>
    setCfg((c) => (c ? { ...c, tiers: c.tiers.map((t, j) => (j === i ? { ...t, [field]: v } : t)) } : c));
  const addTier = () => setCfg((c) => (c ? { ...c, tiers: [...c.tiers, { min_days: 30, percent: 1 }] } : c));
  const removeTier = (i: number) => setCfg((c) => (c ? { ...c, tiers: c.tiers.filter((_, j) => j !== i) } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    setError(null);
    try {
      const clean = {
        enabled: cfg.enabled,
        point_value_rub: Number(cfg.point_value_rub) || 1,
        tiers: cfg.tiers
          .map((t) => ({ min_days: Number(t.min_days) || 0, percent: Number(t.percent) || 0 }))
          .filter((t) => t.min_days >= 1 && t.percent >= 1 && t.percent <= 100)
          .sort((a, b) => a.min_days - b.min_days),
      };
      const updated = await cashbackAdminApi.update(clean);
      setCfg(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return null;
  if (!cfg) return null;

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <div className="space-y-4 rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-bold text-fg"><Coins className="h-5 w-5 text-accent" /> Кэшбэк баллами</h2>
          <p className="mt-1 text-sm text-fg-muted">Начисляется покупателю за каждую оплату (только ₽). % зависит от срока тарифа; баллы = округл(сумма × % / {cfg.point_value_rub}).</p>
        </div>
        <button onClick={save} disabled={saving}
          className="btn-gradient inline-flex shrink-0 items-center gap-2 rounded-xl border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          <Save className="h-4 w-4" /> {saving ? "…" : "Сохранить"}
        </button>
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}
      {saved && <div className="flex items-center gap-2 rounded-xl bg-success/10 px-4 py-3 text-sm text-success"><CheckCircle2 className="h-4 w-4" />Сохранено</div>}

      <button type="button" onClick={() => patch({ enabled: !cfg.enabled })}
        className={`flex w-full items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${cfg.enabled ? "border-accent/30 bg-accent/5" : "border-border-subtle bg-bg"}`}>
        <div>
          <p className="text-sm font-medium text-fg">Кэшбэк баллами</p>
          <p className="mt-0.5 text-xs text-fg-muted">Начислять покупателю баллы за оплату</p>
        </div>
        <span className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${cfg.enabled ? "bg-accent" : "bg-border"}`}>
          <span className={`inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${cfg.enabled ? "translate-x-[22px]" : "translate-x-0.5"}`} />
        </span>
      </button>

      <div className="max-w-xs">
        <label className="mb-1 block text-xs font-medium text-fg-muted">Курс: 1 балл = … ₽</label>
        <input type="number" min={1} value={String(cfg.point_value_rub)}
          onChange={(e) => patch({ point_value_rub: Number(e.target.value) })} className={inputCls} />
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium text-fg-muted">Ступени: срок тарифа (дней) → процент кэшбэка. Берётся максимальная подходящая.</p>
        {cfg.tiers.map((t, i) => (
          <div key={i} className="flex items-end gap-2">
            <div className="flex-1">
              <label className="mb-1 block text-[11px] text-fg-muted">от скольких дней</label>
              <input type="number" min={1} value={String(t.min_days)}
                onChange={(e) => setTier(i, "min_days", Number(e.target.value))} className={inputCls} />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-[11px] text-fg-muted">процент (%)</label>
              <input type="number" min={1} max={100} value={String(t.percent)}
                onChange={(e) => setTier(i, "percent", Number(e.target.value))} className={inputCls} />
            </div>
            <button type="button" onClick={() => removeTier(i)}
              className="mb-0.5 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border-subtle text-fg-muted hover:text-danger">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        <button type="button" onClick={addTier}
          className="inline-flex items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm text-fg-muted hover:text-fg">
          <Plus className="h-4 w-4" /> Добавить ступень
        </button>
      </div>
    </div>
  );
}
