import { useEffect, useState, useCallback } from "react";
import { AlertCircle, KeyRound, X, Settings2, FlaskConical } from "lucide-react";
import { gatewaysAdminApi, type AdminGateway, type GatewayField } from "@/api/admin";
import { ApiError } from "@/types/api";
import { GATEWAY_NAMES } from "@/lib/gatewayNames";
import { useT } from "@/i18n/I18nContext";
import { translate } from "@/i18n/translate";

const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: "₽", USD: "$", EUR: "€", XTR: "⭐",
};

// Имя поля ключа → ключ перевода подписи.
const FIELD_LABELS: Record<string, string> = {
  shop_id: "adm.gateways.f_shop_id",
  api_key: "adm.gateways.f_api_key",
  secret_key: "adm.gateways.f_secret_key",
  merchant_id: "adm.gateways.f_merchant_id",
  wallet_id: "adm.gateways.f_wallet_id",
  customer: "adm.gateways.f_customer",
  vat_code: "adm.gateways.f_vat_code",
  payment_method: "adm.gateways.f_payment_method",
  payment_system_id: "adm.gateways.f_payment_system_id",
  secret_word_2: "adm.gateways.f_secret_word_2",
  customer_email: "adm.gateways.f_customer_email",
  customer_ip: "adm.gateways.f_customer_ip",
  merchant_login: "adm.gateways.f_merchant_login",
  password1: "adm.gateways.f_password1",
  password2: "adm.gateways.f_password2",
};

function fieldLabel(name: string): string {
  const key = FIELD_LABELS[name];
  return key ? translate(key) : name;
}

// ─── Модалка настройки ключей шлюза ──────────────────────────────────────────
function ConfigModal({
  gateway,
  onClose,
  onSaved,
}: {
  gateway: AdminGateway;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [fields, setFields] = useState<GatewayField[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    gatewaysAdminApi
      .fields(gateway.id)
      .then((r) => setFields(r.fields))
      .catch((e) => setErr(e instanceof ApiError ? e.detail : translate("adm.gateways.err_generic")))
      .finally(() => setLoading(false));
  }, [gateway.id]);

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      // Сохраняем только поля, которые админ реально ввёл.
      for (const [name, val] of Object.entries(values)) {
        if (val.trim() === "") continue;
        await gatewaysAdminApi.setField(gateway.id, name, val.trim());
      }
      onSaved();
      onClose();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : t("adm.gateways.save_failed"));
    } finally {
      setSaving(false);
    }
  };

  const name = gateway.display_name || GATEWAY_NAMES[gateway.type] || gateway.type;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40 animate-fade-in" onClick={onClose} />
      <div className="relative z-10 w-full max-w-md rounded-2xl border border-[var(--border)] bg-bg p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-fg">{t("adm.gateways.keys_title", { name })}</h2>
          <button onClick={onClose} aria-label={t("adm.gateways.close")} className="text-fg-subtle hover:text-fg">
            <X className="h-5 w-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
          </div>
        ) : fields.length === 0 ? (
          <p className="py-6 text-center text-sm text-fg-muted">
            {t("adm.gateways.no_fields")}
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {fields.map((f) => (
              <label key={f.name} className="block">
                <span className="mb-1 block text-xs font-medium text-fg-muted">
                  {fieldLabel(f.name)}
                  {f.secret && <span className="text-fg-subtle"> 🔒</span>}
                </span>
                <input
                  type={f.secret ? "password" : "text"}
                  autoComplete="off"
                  value={values[f.name] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                  placeholder={
                    f.is_set
                      ? t("adm.gateways.field_set", { hint: f.hint ?? "••••" })
                      : t("adm.gateways.field_unset")
                  }
                  className="w-full rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2 text-sm text-fg outline-none focus:border-accent"
                />
              </label>
            ))}
            <p className="text-xs text-fg-subtle">
              {t("adm.gateways.fields_hint")}
            </p>
          </div>
        )}

        {err && <p className="mt-3 text-sm text-danger">{err}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="h-9 rounded-xl border border-[var(--border)] px-4 text-sm font-medium text-fg-muted hover:text-fg"
          >
            {t("adm.gateways.cancel")}
          </button>
          {fields.length > 0 && (
            <button
              onClick={save}
              disabled={saving}
              className="btn-gradient inline-flex h-9 items-center rounded-xl px-4 text-sm font-semibold disabled:opacity-60"
            >
              {saving ? t("adm.gateways.saving") : t("adm.gateways.save")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AdminGatewaysPage() {
  const t = useT();
  const [gateways, setGateways] = useState<AdminGateway[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);
  const [configuring, setConfiguring] = useState<AdminGateway | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; text: string }>>({});

  const load = useCallback(() => {
    setLoading(true);
    gatewaysAdminApi.list()
      .then(r => setGateways(r.items))
      .catch(e => setError(e instanceof ApiError ? e.detail : translate("adm.gateways.err_generic")))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (g: AdminGateway) => {
    if (!g.is_configured && !g.is_active) {
      setConfiguring(g); // не настроен — открываем настройку вместо включения
      return;
    }
    setToggling(g.id);
    try {
      await gatewaysAdminApi.toggle(g.id, !g.is_active);
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : t("adm.gateways.err_generic"));
    } finally {
      setToggling(null);
    }
  };

  const test = async (g: AdminGateway) => {
    const name = g.display_name || GATEWAY_NAMES[g.type] || g.type;
    if (!window.confirm(t("adm.gateways.test_confirm", { name }))) return;

    setTesting(g.id);
    setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: t("adm.gateways.test_creating") } }));
    try {
      const res = await gatewaysAdminApi.test(g.id);
      if (res.url) {
        window.open(res.url, "_blank", "noopener");
        setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: t("adm.gateways.test_link_opened") } }));
      } else {
        setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: res.message || t("adm.gateways.test_created") } }));
      }
    } catch (e) {
      setTestResult((r) => ({
        ...r,
        [g.id]: { ok: false, text: e instanceof ApiError ? e.detail : t("adm.gateways.test_failed") },
      }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">{t("adm.gateways.title")}</h1>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-accent/5 px-5 py-4 text-sm text-fg-muted">
        💡 {t("adm.gateways.hint")}
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : gateways.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">{t("adm.gateways.empty")}</div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {gateways.map(g => {
            const name = g.display_name || GATEWAY_NAMES[g.type] || g.type;
            const sym = CURRENCY_SYMBOLS[g.currency] ?? g.currency;
            return (
              <div key={g.id} className={`rounded-2xl border bg-bg-subtle p-5 transition-colors ${g.is_active ? "border-success/30" : "border-border-subtle"}`}>
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <p className="font-semibold text-fg">{name}</p>
                    <p className="text-xs text-fg-muted">{g.currency} {sym}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {g.is_configured ? (
                      <span title={t("adm.gateways.keys_set")} className="text-success"><KeyRound className="h-4 w-4" /></span>
                    ) : (
                      <span title={t("adm.gateways.keys_not_set")} className="text-fg-subtle/70"><KeyRound className="h-4 w-4" /></span>
                    )}
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${g.is_active ? "bg-success/10 text-success" : "bg-fg-subtle/20 text-fg-muted"}`}>
                    {g.is_active ? t("adm.gateways.active") : t("adm.gateways.disabled")}
                  </span>
                  <button
                    onClick={() => toggle(g)}
                    disabled={toggling === g.id}
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${g.is_active ? "bg-success" : "bg-border"}`}
                  >
                    <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${g.is_active ? "translate-x-[22px]" : "translate-x-0.5"}`} />
                  </button>
                </div>

                <div className="mt-3 flex items-center gap-4">
                  <button
                    onClick={() => setConfiguring(g)}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-accent transition-opacity hover:opacity-80"
                  >
                    <Settings2 className="h-3.5 w-3.5" />
                    {t("adm.gateways.configure")}
                  </button>
                  {g.is_configured && (
                    <button
                      onClick={() => test(g)}
                      disabled={testing === g.id}
                      title={t("adm.gateways.test_title")}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-fg-muted transition-opacity hover:text-fg disabled:opacity-50"
                    >
                      <FlaskConical className="h-3.5 w-3.5" />
                      {testing === g.id ? t("adm.gateways.testing") : t("adm.gateways.test_btn")}
                    </button>
                  )}
                </div>

                {testResult[g.id] && (
                  <p className={`mt-2 text-xs ${testResult[g.id]!.ok ? "text-fg-muted" : "text-danger"}`}>
                    {testResult[g.id]!.text}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {configuring && (
        <ConfigModal
          gateway={configuring}
          onClose={() => setConfiguring(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}
