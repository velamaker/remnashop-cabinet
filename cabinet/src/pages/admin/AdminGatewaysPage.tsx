import { useEffect, useState, useCallback } from "react";
import { AlertCircle, KeyRound } from "lucide-react";
import { gatewaysAdminApi, type AdminGateway } from "@/api/admin";
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

export default function AdminGatewaysPage() {
  const [gateways, setGateways] = useState<AdminGateway[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);

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
      alert("Шлюз не настроен. Укажите учётные данные в конфигурации бота.");
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">Платёжные шлюзы</h1>
      </div>

      <div className="rounded-2xl border border-border-subtle bg-accent/5 px-5 py-4 text-sm text-fg-muted">
        💡 Для настройки ключей API редактируйте файл <code className="rounded bg-bg-raised px-1">.env</code> на сервере и перезапустите контейнер.
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

                {!g.is_configured && (
                  <p className="mt-3 text-xs text-warning">⚠ Требуется настройка ключей</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
