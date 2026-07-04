import { useEffect, useState, useCallback } from "react";
import { AlertCircle, KeyRound, X, Settings2, FlaskConical } from "lucide-react";
import { gatewaysAdminApi, type AdminGateway, type GatewayField } from "@/api/admin";
import { ApiError } from "@/types/api";

const GATEWAY_NAMES: Record<string, string> = {
  TELEGRAM_STARS: "Telegram Stars ⭐",
  YOOKASSA: "ЮKassa",
  YOOMONEY: "ЮMoney",
  CRYPTOMUS: "Cryptomus",
  HELEKET: "Heleket",
  CRYPTOPAY: "CryptoPay",
  FREEKASSA: "FreeKassa",
  MULENPAY: "MulenPay",
  PAYMASTER: "PayMaster",
  PLATEGA: "Platega",
  ROBOKASSA: "RoboKassa",
  URLPAY: "UrlPay",
  WATA: "Wata",
  VALUTIX: "Valutix",
};

const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: "₽", USD: "$", EUR: "€", XTR: "⭐",
};

const FIELD_LABELS: Record<string, string> = {
  shop_id: "Shop ID",
  api_key: "API-ключ",
  secret_key: "Секретный ключ",
  merchant_id: "Merchant ID",
  wallet_id: "Wallet ID",
  customer: "Customer",
  vat_code: "Код НДС",
  payment_method: "Метод оплаты (id)",
  payment_system_id: "ID платёжной системы",
  secret_word_2: "Секретное слово 2",
  customer_email: "Email покупателя",
  customer_ip: "IP покупателя",
  merchant_login: "Merchant Login",
  password1: "Пароль 1",
  password2: "Пароль 2",
};

function fieldLabel(name: string): string {
  return FIELD_LABELS[name] || name;
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
  const [fields, setFields] = useState<GatewayField[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    gatewaysAdminApi
      .fields(gateway.id)
      .then((r) => setFields(r.fields))
      .catch((e) => setErr(e instanceof ApiError ? e.detail : "Ошибка"))
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
      setErr(e instanceof ApiError ? e.detail : "Не удалось сохранить");
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
          <h2 className="text-lg font-bold text-fg">Ключи: {name}</h2>
          <button onClick={onClose} aria-label="Закрыть" className="text-fg-subtle hover:text-fg">
            <X className="h-5 w-5" />
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" />
          </div>
        ) : fields.length === 0 ? (
          <p className="py-6 text-center text-sm text-fg-muted">
            Этот шлюз не требует ключей.
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
                  placeholder={f.is_set ? `задано: ${f.hint ?? "••••"}` : "не задано"}
                  className="w-full rounded-xl border border-[var(--border)] bg-bg-subtle px-3 py-2 text-sm text-fg outline-none focus:border-accent"
                />
              </label>
            ))}
            <p className="text-xs text-fg-subtle">
              Пустые поля не меняются. Значения сохраняются в боте.
            </p>
          </div>
        )}

        {err && <p className="mt-3 text-sm text-danger">{err}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="h-9 rounded-xl border border-[var(--border)] px-4 text-sm font-medium text-fg-muted hover:text-fg"
          >
            Отмена
          </button>
          {fields.length > 0 && (
            <button
              onClick={save}
              disabled={saving}
              className="btn-gradient inline-flex h-9 items-center rounded-xl px-4 text-sm font-semibold disabled:opacity-60"
            >
              {saving ? "Сохраняю…" : "Сохранить"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function AdminGatewaysPage() {
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
      .catch(e => setError(e instanceof ApiError ? e.detail : "Ошибка"))
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
      alert(e instanceof ApiError ? e.detail : "Ошибка");
    } finally {
      setToggling(null);
    }
  };

  const test = async (g: AdminGateway) => {
    const name = g.display_name || GATEWAY_NAMES[g.type] || g.type;
    if (!window.confirm(
      `Создать реальный тест-платёж (~2 ₽) для «${name}»?\n\n` +
      `Это проверит, что ключи рабочие. Откроется ссылка оплаты — оплатите ` +
      `и убедитесь, что платёж проходит.`,
    )) return;

    setTesting(g.id);
    setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: "Создаю тест-платёж…" } }));
    try {
      const res = await gatewaysAdminApi.test(g.id);
      if (res.url) {
        window.open(res.url, "_blank", "noopener");
        setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: "Ссылка оплаты открыта в новой вкладке." } }));
      } else {
        setTestResult((r) => ({ ...r, [g.id]: { ok: true, text: res.message || "Тест-платёж создан." } }));
      }
    } catch (e) {
      setTestResult((r) => ({
        ...r,
        [g.id]: { ok: false, text: e instanceof ApiError ? e.detail : "Не удалось создать тест-платёж" },
      }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">Платёжные шлюзы</h1>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-accent/5 px-5 py-4 text-sm text-fg-muted">
        💡 Нажмите «Настроить ключи», чтобы ввести ключи API (сохранятся в боте). Затем
        «Тест 2 ₽» создаёт реальный платёж — единственный способ убедиться, что ключи рабочие.
      </div>

      {error && <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger"><AlertCircle className="h-4 w-4" />{error}</div>}

      {loading ? (
        <div className="flex justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" /></div>
      ) : gateways.length === 0 ? (
        <div className="py-20 text-center text-fg-muted">Шлюзы не найдены</div>
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
                      <span title="Ключи настроены" className="text-success"><KeyRound className="h-4 w-4" /></span>
                    ) : (
                      <span title="Ключи не настроены" className="text-fg-subtle/70"><KeyRound className="h-4 w-4" /></span>
                    )}
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${g.is_active ? "bg-success/10 text-success" : "bg-fg-subtle/20 text-fg-muted"}`}>
                    {g.is_active ? "Активен" : "Выключен"}
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
                    Настроить ключи
                  </button>
                  {g.is_configured && (
                    <button
                      onClick={() => test(g)}
                      disabled={testing === g.id}
                      title="Создать реальный тест-платёж ~2 ₽, чтобы проверить ключи"
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-fg-muted transition-opacity hover:text-fg disabled:opacity-50"
                    >
                      <FlaskConical className="h-3.5 w-3.5" />
                      {testing === g.id ? "Тест…" : "Тест 2 ₽"}
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
