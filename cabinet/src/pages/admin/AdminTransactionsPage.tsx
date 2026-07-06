import { useEffect, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, AlertCircle, Filter, Download, X } from "lucide-react";
import { transactionsAdminApi, type AdminTransaction, type AdminTransactionDetail } from "@/api/admin";
import { ApiError } from "@/types/api";
import { formatDate } from "@/lib/format";

const LIMIT = 25;

const STATUS_OPTIONS = ["", "PENDING", "COMPLETED", "FAILED", "CANCELLED"];
const GATEWAY_OPTIONS = ["", "YOOKASSA", "TELEGRAM_STARS", "CRYPTOMUS"];

function statusStyle(status: string): string {
  switch (status) {
    case "COMPLETED":
      return "bg-success/10 text-success";
    case "PENDING":
      return "bg-warning/10 text-warning";
    case "FAILED":
    case "CANCELLED":
      return "bg-danger/10 text-danger";
    default:
      return "bg-bg-raised text-fg-muted";
  }
}

// Сумма как есть, с разделителем тысяч (значение приходит строкой из pricing).
function formatAmount(amount: string): string {
  const n = Number(amount);
  return Number.isFinite(n) ? n.toLocaleString("ru-RU") : amount;
}

const CURRENCY_SYMBOL: Record<string, string> = {
  RUB: "₽",
  USD: "$",
  EUR: "€",
  XTR: "⭐", // Telegram Stars
};

function currencySymbol(currency: string | null): string {
  if (!currency) return "";
  return CURRENCY_SYMBOL[currency.toUpperCase()] ?? currency;
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border-subtle py-2 last:border-0">
      <span className="text-xs text-fg-muted">{label}</span>
      <span className="max-w-[60%] break-words text-right text-sm text-fg">{value ?? "—"}</span>
    </div>
  );
}

function TransactionDetailModal({ paymentId, onClose }: { paymentId: string; onClose: () => void }) {
  const [d, setD] = useState<AdminTransactionDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    transactionsAdminApi.get(paymentId).then(setD).catch((e) => setErr(e instanceof ApiError ? e.detail : "Ошибка"));
  }, [paymentId]);

  const pricing = d?.pricing as Record<string, unknown> | null | undefined;
  const plan = d?.plan_snapshot as Record<string, unknown> | null | undefined;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
      <div className="surface max-h-[85vh] w-full max-w-lg overflow-y-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">Детали транзакции</h2>
          <button onClick={onClose} className="text-fg-subtle hover:text-fg"><X className="h-5 w-5" /></button>
        </div>
        {err && <p className="text-sm text-danger">{err}</p>}
        {!d && !err && <div className="py-8 text-center"><div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-border border-t-accent" /></div>}
        {d && (
          <div className="space-y-4">
            <div>
              <DetailRow label="Статус" value={<span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusStyle(d.status)}`}>{d.status}</span>} />
              <DetailRow label="Тип покупки" value={d.purchase_type} />
              <DetailRow label="Тест" value={d.is_test ? "Да" : "Нет"} />
              <DetailRow label="Валюта" value={d.currency} />
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Оплата</p>
              <DetailRow label="Сумма (итог)" value={String(pricing?.final_amount ?? "—")} />
              <DetailRow label="Сумма (до скидки)" value={String(pricing?.original_amount ?? "—")} />
              <DetailRow label="Скидка, %" value={String(pricing?.discount_percent ?? 0)} />
              <DetailRow label="Бесплатно" value={pricing?.is_free ? "Да" : "Нет"} />
              <DetailRow label="Шлюз" value={`${d.gateway_type}${d.gateway_display_name ? ` (${d.gateway_display_name})` : ""}`} />
              {d.payment_method && <DetailRow label="Метод" value={d.payment_method} />}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Тариф</p>
              <DetailRow label="Название" value={String(plan?.name ?? "—")} />
              <DetailRow label="Срок, дней" value={String(plan?.duration ?? "—")} />
              <DetailRow label="Устройств" value={String(plan?.device_limit ?? "—")} />
              <DetailRow label="Трафик" value={plan?.traffic_limit === 0 ? "Безлимит" : String(plan?.traffic_limit ?? "—")} />
              <DetailRow label="Пробный" value={plan?.is_trial ? "Да" : "Нет"} />
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Пользователь</p>
              <DetailRow label="Имя" value={d.user.name ?? `#${d.user.id}`} />
              {d.user.email && <DetailRow label="Email" value={d.user.email} />}
              {d.user.username && <DetailRow label="Telegram" value={`@${d.user.username}`} />}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Тайминги</p>
              <DetailRow label="Создана" value={d.created_at ? formatDate(d.created_at) : "—"} />
              <DetailRow label="Обновлена" value={d.updated_at ? formatDate(d.updated_at) : "—"} />
              <DetailRow label="ID платежа" value={<span className="font-mono text-xs break-all">{d.payment_id}</span>} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AdminTransactionsPage() {
  const [items, setItems] = useState<AdminTransaction[]>([]);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [gateway, setGateway] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const onExport = async () => {
    setExporting(true);
    setError(null);
    try {
      await transactionsAdminApi.exportXlsx({
        status: status || undefined,
        gateway: gateway || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Экспорт не удался");
    } finally {
      setExporting(false);
    }
  };

  const load = useCallback(() => {
    setLoading(true);
    transactionsAdminApi
      .list({
        limit: LIMIT,
        offset,
        status: status || undefined,
        gateway: gateway || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [offset, status, gateway, dateFrom, dateTo]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-fg">Транзакции</h1>
        <div className="flex flex-shrink-0 items-center gap-3">
          <span className="hidden text-sm text-fg-muted sm:inline">{total} всего</span>
          <button
            onClick={onExport}
            disabled={exporting || total === 0}
            className="inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay disabled:opacity-50"
            title="Скачать Excel (.xlsx) с учётом фильтров"
          >
            <Download className="h-4 w-4" />
            {exporting ? "Готовим…" : "Экспорт Excel"}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-fg-muted" />
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
            className="rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "Все статусы"}
              </option>
            ))}
          </select>
        </div>
        <select
          value={gateway}
          onChange={(e) => {
            setGateway(e.target.value);
            setOffset(0);
          }}
          className="rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
        >
          {GATEWAY_OPTIONS.map((g) => (
            <option key={g} value={g}>
              {g || "Все шлюзы"}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-fg-muted">с</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setOffset(0); }}
            className="rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          />
          <span className="text-xs text-fg-muted">по</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setOffset(0); }}
            className="rounded-xl border border-border-subtle bg-bg-subtle px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          />
          {(dateFrom || dateTo) && (
            <button
              type="button"
              onClick={() => { setDateFrom(""); setDateTo(""); setOffset(0); }}
              className="rounded-lg px-2 py-1 text-xs text-fg-muted hover:text-fg"
            >
              сброс
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl bg-danger/10 px-4 py-3 text-sm text-danger">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-border-subtle">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle bg-bg-subtle">
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">ID платежа</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Пользователь</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Статус</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted">Сумма</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden md:table-cell">Тариф</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden sm:table-cell">Шлюз</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden lg:table-cell">Тип</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden lg:table-cell">Дата</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center">
                    <div className="inline-block h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-fg-muted">
                    Транзакции не найдены
                  </td>
                </tr>
              ) : (
                items.map((t, i) => (
                  <tr
                    key={t.payment_id ?? `tx-${i}`}
                    onClick={() => t.payment_id && setDetailId(t.payment_id)}
                    className="border-b border-border-subtle last:border-0 hover:bg-bg-raised transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-fg-muted truncate max-w-[100px]">
                      {t.payment_id ? `${t.payment_id.slice(0, 8)}…` : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div>
                        <p className="font-medium text-fg">{t.user_name ?? (t.user_id != null ? `#${t.user_id}` : "—")}</p>
                        {t.user_email && (
                          <p className="text-xs text-fg-muted">{t.user_email}</p>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusStyle(t.status)}`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap font-semibold text-fg">
                      {t.amount != null && t.amount !== ""
                        ? `${formatAmount(t.amount)} ${currencySymbol(t.currency)}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 hidden md:table-cell">
                      {t.plan_name ? (
                        <div>
                          <p className="text-fg">{t.plan_name}</p>
                          {t.plan_duration != null && (
                            <p className="text-xs text-fg-subtle">{t.plan_duration} дн.</p>
                          )}
                        </div>
                      ) : (
                        <span className="text-fg-muted">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-fg-muted hidden sm:table-cell">
                      {t.gateway_type}
                    </td>
                    <td className="px-4 py-3 text-fg-muted hidden lg:table-cell">
                      {t.purchase_type}
                      {t.is_test && (
                        <span className="ml-1 rounded bg-warning/10 px-1 text-xs text-warning">
                          TEST
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-fg-muted hidden lg:table-cell">
                      {t.created_at ? formatDate(t.created_at) : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-fg-muted">
            Страница {currentPage} из {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              disabled={offset === 0 || loading}
              className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button
              onClick={() => setOffset(offset + LIMIT)}
              disabled={offset + LIMIT >= total || loading}
              className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {detailId && <TransactionDetailModal paymentId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}
