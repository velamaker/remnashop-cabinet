import { useEffect, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, AlertCircle, Filter } from "lucide-react";
import { transactionsAdminApi, type AdminTransaction } from "@/api/admin";
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

export default function AdminTransactionsPage() {
  const [items, setItems] = useState<AdminTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [gateway, setGateway] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    transactionsAdminApi
      .list({
        limit: LIMIT,
        offset,
        status: status || undefined,
        gateway: gateway || undefined,
      })
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Ошибка"))
      .finally(() => setLoading(false));
  }, [offset, status, gateway]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-fg">Транзакции</h1>
        <span className="text-sm text-fg-muted">{total} всего</span>
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
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden sm:table-cell">Шлюз</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden md:table-cell">Тип</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-fg-muted hidden lg:table-cell">Дата</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center">
                    <div className="inline-block h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-fg-muted">
                    Транзакции не найдены
                  </td>
                </tr>
              ) : (
                items.map((t, i) => (
                  <tr
                    key={t.payment_id ?? `tx-${i}`}
                    className="border-b border-border-subtle last:border-0 hover:bg-bg-raised transition-colors"
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
                    <td className="px-4 py-3 text-fg-muted hidden sm:table-cell">
                      {t.gateway_type}
                    </td>
                    <td className="px-4 py-3 text-fg-muted hidden md:table-cell">
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
    </div>
  );
}
