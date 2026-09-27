import { useEffect, useState } from "react";
import { UsersRound } from "lucide-react";
import {
  familyAdminApi,
  type FamilyAdminConfig,
  type FamilyAdminPlan,
  type FamilyAdminResponse,
  type FamilyTerms,
} from "@/api/admin";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

/**
 * «Семейные профили» — тумблер функции, отсрочка удаления и какие тарифы семейные.
 *
 * Тариф становится семейным, когда у него заданы условия «N профилей × D устройств
 * на профиль». Трафик каждому профилю — тарифный целиком, лимит устройств владельца
 * профили не трогают (решение владельца продукта). По умолчанию ни один тариф не
 * семейный и функция выключена.
 *
 * «Сделать обычным» приостанавливает профили владельцев этого тарифа, а через
 * отсрочку удаляет их — поэтому спрашиваем подтверждение.
 */

const SECTION = "rounded-2xl border border-border-subtle bg-bg-subtle p-5";
const INPUT =
  "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
const BUTTON =
  "inline-flex shrink-0 items-center gap-2 rounded-xl border border-border-subtle px-3 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-50";

const MIN = 1;
const MAX = 10;

function inRange(value: number): boolean {
  return Number.isInteger(value) && value >= MIN && value <= MAX;
}

function PlanRow({
  plan,
  graceDays,
  onChanged,
}: {
  plan: FamilyAdminPlan;
  graceDays: number;
  onChanged: (terms: FamilyTerms | null) => void;
}) {
  const t = useT();
  const [profiles, setProfiles] = useState(plan.terms?.max_profiles ?? 3);
  const [devices, setDevices] = useState(plan.terms?.devices_per_profile ?? Math.max(1, Math.min(plan.device_limit || 2, MAX)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const valid = inRange(profiles) && inRange(devices);
  const title = plan.deleted ? t("adm.family.deleted_plan", { id: plan.id }) : plan.name;

  const save = async () => {
    if (!valid) {
      setError(t("adm.family.range_error"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await familyAdminApi.setTerms(plan.id, { max_profiles: profiles, devices_per_profile: devices });
      onChanged(res.terms);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.family.save_error"));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!window.confirm(t("adm.family.confirm_regular", { plan: title, days: graceDays }))) return;
    setBusy(true);
    setError(null);
    try {
      await familyAdminApi.clearTerms(plan.id);
      onChanged(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.family.save_error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-col gap-3 border-t border-border-subtle py-3 first:border-t-0 sm:flex-row sm:items-end">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-fg">
          {title}
          {!plan.deleted && !plan.is_active && (
            <span className="ml-2 text-xs text-fg-muted">({t("adm.family.inactive")})</span>
          )}
        </p>
        <p className="mt-0.5 text-xs text-fg-muted">
          {plan.terms
            ? t("adm.family.terms_row", {
                profiles: plan.terms.max_profiles,
                devices: plan.terms.devices_per_profile,
              })
            : t("adm.family.regular")}
        </p>
        {plan.is_trial && <p className="mt-0.5 text-xs text-fg-muted">{t("adm.family.trial_note")}</p>}
        {plan.deleted && <p className="mt-0.5 text-xs text-fg-muted">{t("adm.family.deleted_note")}</p>}
        {error && <p className="mt-1 text-xs text-danger">{error}</p>}
      </div>
      {plan.deleted && plan.terms && (
        <button onClick={clear} disabled={busy} className={BUTTON}>
          {t("adm.family.make_regular")}
        </button>
      )}
      {!plan.is_trial && !plan.deleted && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="w-24">
            <span className="mb-1 block text-xs text-fg-muted">{t("adm.family.max_profiles")}</span>
            <input
              type="number"
              min={MIN}
              max={MAX}
              value={profiles}
              onChange={(e) => setProfiles(Number(e.target.value))}
              className={INPUT}
            />
          </label>
          <label className="w-28">
            <span className="mb-1 block text-xs text-fg-muted">{t("adm.family.devices_per_profile")}</span>
            <input
              type="number"
              min={MIN}
              max={MAX}
              value={devices}
              onChange={(e) => setDevices(Number(e.target.value))}
              className={INPUT}
            />
          </label>
          <button onClick={save} disabled={busy || !valid} className={BUTTON}>
            {plan.terms ? t("adm.family.update") : t("adm.family.make_family")}
          </button>
          {plan.terms && (
            <button onClick={clear} disabled={busy} className={BUTTON}>
              {t("adm.family.make_regular")}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export function AdminFamilyPage() {
  const t = useT();
  const [data, setData] = useState<FamilyAdminResponse | null>(null);
  const [form, setForm] = useState<FamilyAdminConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    familyAdminApi
      .get()
      .then((res) => {
        setData(res);
        setForm(res.config);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : t("adm.family.load_error")));
    // перевод берём на момент загрузки; перезапрашивать настройки при смене языка не нужно
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (!form) return;
    setSaving(true);
    setNote(null);
    setError(null);
    try {
      const res = await familyAdminApi.update(form);
      setForm(res.config);
      setNote(t("adm.family.saved"));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("adm.family.save_error"));
    } finally {
      setSaving(false);
    }
  };

  if (error && !form) return <p className="text-sm text-danger">{error}</p>;
  if (!form || !data) return <p className="text-sm text-fg-muted">{t("adm.family.loading")}</p>;

  const summary = data.summary ?? {};
  const setPlanTerms = (planId: number, terms: FamilyTerms | null) =>
    setData((prev) =>
      prev ? { ...prev, plans: prev.plans.map((p) => (p.id === planId ? { ...p, terms } : p)) } : prev,
    );

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <UsersRound className="h-5 w-5 text-accent" />
        <h1 className="text-xl font-semibold text-fg">{t("adm.family.title")}</h1>
      </div>

      <section className={SECTION}>
        <label className="flex cursor-pointer items-start gap-3 py-2">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
          />
          <span className="min-w-0">
            <span className="block text-sm text-fg">{t("adm.family.enabled_label")}</span>
            <span className="mt-0.5 block text-xs text-fg-muted">{t("adm.family.enabled_hint")}</span>
          </span>
        </label>

        <div className="mt-3 max-w-xs">
          <label htmlFor="family-grace" className="mb-1 block text-xs text-fg-muted">
            {t("adm.family.grace_label")}
          </label>
          <input
            id="family-grace"
            type="number"
            min={1}
            max={365}
            value={form.suspend_grace_days}
            onChange={(e) => setForm({ ...form, suspend_grace_days: Number(e.target.value) || 1 })}
            className={INPUT}
          />
          <p className="mt-1 text-xs text-fg-muted">{t("adm.family.grace_hint")}</p>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button onClick={save} disabled={saving} className={BUTTON}>
            {saving ? "…" : t("adm.family.save")}
          </button>
          {note && <span className="text-xs text-fg-muted">{note}</span>}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </section>

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">{t("adm.family.plans_title")}</h3>
        <p className="mt-0.5 text-xs text-fg-muted">{t("adm.family.plans_hint")}</p>
        <p className="mt-1 text-xs text-fg-muted">{t("adm.family.period_note")}</p>
        {data.plans.length === 0 ? (
          <p className="mt-3 text-sm text-fg-muted">{t("adm.family.plans_empty")}</p>
        ) : (
          <ul className="mt-2">
            {data.plans.filter((plan) => !(plan.deleted && !plan.terms)).map((plan) => (
              <PlanRow
                key={plan.id}
                plan={plan}
                graceDays={form.suspend_grace_days}
                onChanged={(terms) => setPlanTerms(plan.id, terms)}
              />
            ))}
          </ul>
        )}
      </section>

      <section className={SECTION}>
        <h3 className="text-sm font-semibold text-fg">{t("adm.family.stats_title")}</h3>
        <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {(
            [
              ["adm.family.stat_owners", summary.owners],
              ["adm.family.stat_active", summary.active],
              ["adm.family.stat_suspended", summary.suspended],
              ["adm.family.stat_pending", summary.pending],
              ["adm.family.stat_failing", summary.failing],
            ] as const
          ).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-fg-muted">{t(key)}</dt>
              <dd className="tabular text-lg font-semibold text-fg">{value ?? 0}</dd>
            </div>
          ))}
        </dl>
      </section>

      <p className="text-xs leading-relaxed text-fg-muted">{t("adm.family.footer")}</p>
    </div>
  );
}

export default AdminFamilyPage;
