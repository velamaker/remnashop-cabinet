import { GiftCard } from "@/components/GiftCard";
import { useEffect, useState, useCallback } from "react";
import { Wallet, TrendingUp, ShoppingBag, ChevronLeft, ChevronRight, AlertCircle, CreditCard, PlusCircle } from "lucide-react";
import { balanceApi, POINT_VALUE_RUB, type BalanceResponse, type BalanceTransaction, type TopupConfig } from "@/api/balance";
import { subscriptionApi } from "@/api/subscription";
import { ApiError, type PlanOfferResponse } from "@/types/api";
import { formatDate, activeLocale } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

const LIMIT = 15;

const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: "₽",
  USD: "$",
  EUR: "€",
  XTR: "⭐",
};

function currencySymbol(currency: string): string {
  return CURRENCY_SYMBOLS[currency?.toUpperCase()] ?? currency;
}

function statusStyle(status: string): string {
  switch (status.toUpperCase()) {
    case "COMPLETED":
      return "bg-success/10 text-success";
    case "PENDING":
      return "bg-warning/10 text-warning";
    case "CANCELED":
    case "FAILED":
      return "bg-danger/10 text-danger";
    default:
      return "bg-bg-raised text-fg-muted";
  }
}

function statusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case "COMPLETED": return "balance.stCompleted";
    case "PENDING": return "balance.stPending";
    case "CANCELED": return "balance.stCanceled";
    case "FAILED": return "balance.stFailed";
    default: return status;
  }
}

function gatewayLabel(type: string, displayName: string | null): string {
  if (displayName) return displayName;
  switch (type.toUpperCase()) {
    case "YOOKASSA": return "ЮKassa";
    case "TELEGRAM_STARS": return "Telegram Stars";
    case "CRYPTOMUS": return "Cryptomus";
    default: return type;
  }
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl p-5 ${
        accent
          ? "border-0 bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_12px_30px_-12px_var(--accent-glow)]"
          : "border border-border-subtle bg-bg-subtle"
      }`}
    >
      <div className="mb-3 flex items-center gap-2">
        <div
          className={`flex h-8 w-8 items-center justify-center rounded-xl ${
            accent ? "bg-white/20 text-white" : "bg-bg-raised text-fg-muted"
          }`}
        >
          <Icon className="h-4 w-4" strokeWidth={2} />
        </div>
        <span className={`text-xs font-medium ${accent ? "text-white/85" : "text-fg-muted"}`}>{label}</span>
      </div>
      <p className={`text-2xl font-bold ${accent ? "text-white" : "text-fg"}`}>{value}</p>
      {sub && <p className={`mt-1 text-xs ${accent ? "text-white/75" : "text-fg-subtle"}`}>{sub}</p>}
    </div>
  );
}

function TransactionRow({ t }: { t: BalanceTransaction }) {
  const tr = useT();
  const sym = currencySymbol(t.currency);
  const isFree = t.is_free || t.final_amount === "0";

  return (
    <div className="flex items-center justify-between gap-4 border-b border-border-subtle py-4 last:border-0">
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-fg">
            {t.plan_name ?? gatewayLabel(t.gateway_type, t.gateway_display_name)}
          </span>
          {t.is_test && (
            <span className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning">
              TEST
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-fg-muted">
            {gatewayLabel(t.gateway_type, t.gateway_display_name)}
          </span>
          {t.created_at && (
            <span className="text-xs text-fg-subtle">· {formatDate(t.created_at)}</span>
          )}
        </div>
      </div>

      <div className="flex flex-shrink-0 flex-col items-end gap-1">
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusStyle(t.status)}`}
        >
          {tr(statusLabel(t.status))}
        </span>
        <span className="text-sm font-semibold text-fg">
          {isFree ? (
            <span className="text-success">{tr("billing.free")}</span>
          ) : (
            <>
              {t.final_amount} {sym}
              {t.discount_percent > 0 && (
                <span className="ml-1 text-xs font-normal text-fg-muted line-through">
                  {t.original_amount} {sym}
                </span>
              )}
            </>
          )}
        </span>
      </div>
    </div>
  );
}

function ConvertPoints({ points, onConverted }: { points: number; onConverted: (balance: number, points: number) => void }) {
  const tr = useT();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  if (points <= 0) return null;
  const rub = points * POINT_VALUE_RUB;

  const convert = async () => {
    setErr(null);
    setMsg(null);
    setBusy(true);
    try {
      const r = await balanceApi.convertPoints(points);
      setMsg(tr("balance.convertOk", { r: r.credited_rub }));
      onConverted(r.balance, r.points);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : tr("balance.errLoad"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-bg-subtle p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{tr("balance.convertHave", { n: points })}</p>
        {(err || msg) && <p className={`mt-1 text-xs ${err ? "text-danger" : "text-success"}`}>{err ?? msg}</p>}
      </div>
      <button
        onClick={convert}
        disabled={busy}
        className="inline-flex flex-shrink-0 items-center gap-2 rounded-xl btn-gradient border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {busy ? "…" : tr("balance.convertBtn", { r: rub })}
      </button>
    </div>
  );
}

function AutopayToggle({ enabled, onChange }: { enabled: boolean; onChange: (v: boolean) => void }) {
  const tr = useT();
  const [busy, setBusy] = useState(false);

  const toggle = async () => {
    setBusy(true);
    try {
      const r = await balanceApi.setAutopay(!enabled);
      onChange(r.autopay_enabled);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-border-subtle bg-bg-subtle p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{tr("balance.autopayTitle")}</p>
        <p className="mt-0.5 text-xs text-fg-muted">{tr("balance.autopaySub")}</p>
      </div>
      <button
        type="button"
        onClick={toggle}
        disabled={busy}
        role="switch"
        aria-checked={enabled}
        className={`relative h-6 w-11 flex-shrink-0 rounded-full transition-colors disabled:opacity-50 ${enabled ? "bg-accent" : "bg-bg-overlay border border-[var(--border)]"}`}
      >
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${enabled ? "left-[22px]" : "left-0.5"}`} />
      </button>
    </div>
  );
}

function TopupCard() {
  const [cfg, setCfg] = useState<TopupConfig | null>(null);
  const [amount, setAmount] = useState<string>("");
  const [gateway, setGateway] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const tr = useT();

  useEffect(() => {
    balanceApi
      .topupConfig()
      .then((c) => {
        setCfg(c);
        if (c.presets[0]) setAmount(String(c.presets[0]));
        if (c.gateways[0]) setGateway(c.gateways[0].gateway_type);
      })
      .catch(() => {});
  }, []);

  if (!cfg || !cfg.enabled || cfg.gateways.length === 0) return null;

  const amt = Number(amount);
  const valid = Number.isFinite(amt) && amt >= cfg.min_amount && amt <= cfg.max_amount;
  const bonus = valid && cfg.bonus_percent > 0 ? Math.round(amt * cfg.bonus_percent) / 100 : 0;

  const pay = async () => {
    setErr(null);
    if (!valid || !gateway) return;
    setBusy(true);
    try {
      const r = await balanceApi.createTopup(amt, gateway);
      if (r.payment_url) {
        window.location.href = r.payment_url;
      } else {
        setErr(tr("balance.topupNoUrl"));
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : tr("balance.topupErr"));
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <h2 className="flex items-center gap-2 text-base font-semibold text-fg">
        <PlusCircle className="h-4 w-4 text-accent" /> {tr("balance.topupTitle")}
      </h2>
      <p className="mb-4 mt-0.5 text-xs text-fg-muted">
        {cfg.bonus_percent > 0
          ? tr("balance.topupBonusNote", { percent: cfg.bonus_percent })
          : tr("balance.topupNote")}
      </p>

      <div className="mb-3 flex flex-wrap gap-2">
        {cfg.presets.map((p) => {
          const on = amount === String(p);
          return (
            <button
              key={p}
              type="button"
              onClick={() => setAmount(String(p))}
              className={`rounded-xl px-3 py-2 text-sm font-medium transition-all ${
                on ? "btn-gradient border-0 text-white" : "border border-border-subtle bg-bg-raised text-fg hover:border-[var(--border)]"
              }`}
            >
              {p} ₽
            </button>
          );
        })}
      </div>

      <div className="mb-3 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-fg-muted">{tr("balance.topupAmount", { min: cfg.min_amount, max: cfg.max_amount })}</label>
          <input
            type="number"
            min={cfg.min_amount}
            max={cfg.max_amount}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        {cfg.gateways.length > 1 && (
          <div>
            <label className="mb-1 block text-xs text-fg-muted">{tr("balance.topupGateway")}</label>
            <select
              value={gateway}
              onChange={(e) => setGateway(e.target.value)}
              className="w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {cfg.gateways.map((g) => (
                <option key={g.gateway_type} value={g.gateway_type}>{g.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-fg-muted">
          {valid && bonus > 0
            ? tr("balance.topupCredited", {
                total: (amt + bonus).toLocaleString(activeLocale(), { maximumFractionDigits: 2 }),
                bonus,
              })
            : !valid && amount
              ? tr("balance.topupRange", { min: cfg.min_amount, max: cfg.max_amount })
              : ""}
        </span>
        <button
          onClick={pay}
          disabled={busy || !valid || !gateway}
          className="inline-flex items-center gap-2 rounded-xl btn-gradient border-0 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "…" : tr("balance.topupBtn")}
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-danger">{err}</p>}
    </div>
  );
}

function rubPrice(prices: { currency: string; final_amount: string }[]): number | null {
  const p = prices.find((x) => x.currency === "RUB");
  return p ? Number(p.final_amount) : null;
}

function RenewFromBalance({ balance, onSpent }: { balance: number; onSpent: (b: number) => void }) {
  const tr = useT();
  const [plan, setPlan] = useState<PlanOfferResponse | null>(null);
  const [days, setDays] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    subscriptionApi
      .offers()
      .then((o) => {
        // Текущий тариф = тот, для которого продление помечено как RENEW.
        const current = o.plans.find((p) => p.recommended_purchase_type === "RENEW") ?? null;
        setPlan(current);
        if (current && current.durations[0]) setDays(current.durations[0].days);
      })
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  if (!loaded || !plan || plan.durations.length === 0) return null;

  const selected = plan.durations.find((d) => d.days === days) ?? null;
  const price = selected ? rubPrice(selected.prices) : null;
  const enough = price != null && balance >= price;

  const apply = async () => {
    setErr(null);
    setMsg(null);
    if (days == null || !enough) return;
    if (!confirm) return setConfirm(true);
    setBusy(true);
    try {
      const r = await balanceApi.spendOnRenewal(days);
      setMsg(tr("balance.renewOk", { d: r.days_added }));
      setConfirm(false);
      onSpent(r.balance);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : tr("balance.errLoad"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-5">
      <h2 className="text-base font-semibold text-fg">{tr("balance.renewTitle")}</h2>
      <p className="mb-4 mt-0.5 text-xs text-fg-muted">{tr("balance.renewSub", { plan: plan.name })}</p>

      <div className="mb-4 flex flex-wrap gap-2">
        {plan.durations.map((d) => {
          const pr = rubPrice(d.prices);
          const on = d.days === days;
          const afford = pr != null && balance >= pr;
          return (
            <button
              key={d.days}
              type="button"
              onClick={() => { setDays(d.days); setConfirm(false); setMsg(null); }}
              className={`flex flex-col items-start rounded-xl px-3 py-2 text-left transition-all ${
                on ? "btn-gradient border-0" : "border border-border-subtle bg-bg-raised hover:border-[var(--border)]"
              }`}
            >
              <span className={`text-sm font-medium ${on ? "text-white" : "text-fg"}`}>{d.days} {tr("balance.daysShort")}</span>
              <span className={`text-xs ${on ? "text-white/85" : afford ? "text-fg-muted" : "text-danger"}`}>
                {pr != null ? `${pr} ₽` : "—"}
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-fg-muted">
          {price != null && !enough ? tr("balance.notEnough") : ""}
        </span>
        <button
          onClick={apply}
          disabled={busy || days == null || !enough}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition disabled:opacity-50 ${
            confirm ? "bg-danger text-white hover:opacity-90" : "btn-gradient border-0 text-white"
          }`}
        >
          {busy ? "…" : confirm ? tr("balance.renewConfirm", { p: price ?? 0 }) : tr("balance.renewPay")}
        </button>
      </div>
      {err && <p className="mt-2 text-sm text-danger">{err}</p>}
      {msg && <p className="mt-2 text-sm text-success">{msg}</p>}
    </div>
  );
}

export default function BalancePage() {
  const tr = useT();
  const [balance, setBalance] = useState<BalanceResponse | null>(null);
  const [transactions, setTransactions] = useState<BalanceTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [txLoading, setTxLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    balanceApi
      .get()
      .then(setBalance)
      .catch((e) => setError(e instanceof ApiError ? e.detail : tr("balance.errLoad")))
      .finally(() => setLoading(false));
  }, []);

  const loadTx = useCallback(() => {
    setTxLoading(true);
    balanceApi
      .transactions({ limit: LIMIT, offset })
      .then((res) => {
        setTransactions(res.items);
        setTotal(res.total);
      })
      .catch((e) => setError(e instanceof ApiError ? e.detail : tr("balance.errLoad")))
      .finally(() => setTxLoading(false));
  }, [offset]);

  useEffect(() => {
    loadTx();
  }, [loadTx]);

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  if (loading) {
    return (
      <div className="flex min-h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center gap-3 py-20 text-center">
        <AlertCircle className="h-10 w-10 text-danger" />
        <p className="text-fg-muted">{error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-bold text-fg">{tr("nav.balance")}</h1>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          icon={CreditCard}
          label={tr("balance.walletTitle")}
          value={`${(balance?.balance ?? 0).toLocaleString(activeLocale(), { maximumFractionDigits: 2 })} ₽`}
          sub={tr("balance.walletSub")}
          accent
        />
        <StatCard
          icon={Wallet}
          label={tr("sub.points")}
          value={String(balance?.points ?? 0)}
          sub={tr("balance.pointsSub")}
        />
        <StatCard
          icon={TrendingUp}
          label={tr("balance.spentTotal")}
          value={`${balance?.total_spent?.toLocaleString(activeLocale(), { maximumFractionDigits: 2 }) ?? "0"} ₽`}
          sub={tr("balance.spentSub")}
        />
        <StatCard
          icon={ShoppingBag}
          label={tr("sub.purchases")}
          value={String(balance?.total_purchases ?? 0)}
          sub={tr("balance.purchasesSub")}
        />
      </div>

      {/* Пополнить баланс через шлюз (скроется, если выключено/нет шлюзов) */}
      <TopupCard />

      {/* Подарить подписку с баланса */}
      <GiftCard />

      {/* Перевести баллы рефералки в рубли (скроется, если баллов нет) */}
      <ConvertPoints
        points={balance?.points ?? 0}
        onConverted={(bal, pts) => setBalance((prev) => (prev ? { ...prev, balance: bal, points: pts } : prev))}
      />

      {/* Продлить с баланса (компонент сам скроется, если нет подписки для продления) */}
      <RenewFromBalance
        balance={balance?.balance ?? 0}
        onSpent={(b) => setBalance((prev) => (prev ? { ...prev, balance: b } : prev))}
      />

      {/* Автопродление с баланса */}
      {balance && (
        <AutopayToggle
          enabled={balance.autopay_enabled}
          onChange={(v) => setBalance((prev) => (prev ? { ...prev, autopay_enabled: v } : prev))}
        />
      )}

      {/* Transactions */}
      <div>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">{tr("balance.history")}</h2>
          <span className="text-sm text-fg-muted">{tr("balance.totalCount", { n: total })}</span>
        </div>

        <div className="rounded-2xl border border-border-subtle bg-bg-subtle px-5">
          {txLoading ? (
            <div className="flex justify-center py-12">
              <div className="h-7 w-7 animate-spin rounded-full border-2 border-border border-t-accent" />
            </div>
          ) : transactions.length === 0 ? (
            <div className="py-12 text-center text-sm text-fg-muted">
              {tr("balance.noTx")}
            </div>
          ) : (
            transactions.map((t) => <TransactionRow key={t.payment_id} t={t} />)
          )}
        </div>

        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between">
            <p className="text-xs text-fg-muted">
              {tr("balance.pageOf", { cur: currentPage, total: totalPages })}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                disabled={offset === 0 || txLoading}
                className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                onClick={() => setOffset(offset + LIMIT)}
                disabled={offset + LIMIT >= total || txLoading}
                className="rounded-xl border border-border-subtle p-2 text-fg-muted hover:text-fg disabled:opacity-40 transition-colors"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
