import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Gift, Loader2, Copy, Check, RefreshCw, Clock } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { giftApi, type GiftHistoryItem, type GiftResult } from "@/api/gift";
import type { SubscriptionOffersResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

/** Названия шлюзов: в offers.gateways приходит только тип, человеческого имени там нет. */
function gatewayLabel(type: string): string {
  switch (type.toUpperCase()) {
    case "YOOKASSA": return "ЮKassa";
    case "YOOMONEY": return "ЮMoney";
    case "TELEGRAM_STARS": return "Telegram Stars";
    case "CRYPTOMUS": return "Cryptomus";
    default: return type;
  }
}

const BALANCE = "balance";

/**
 * «Подарить подписку»: даритель выбирает тариф+срок и способ оплаты — списать с ₽-баланса
 * или заплатить через шлюз. Генерируется одноразовый код; получатель активирует его
 * обычным вводом промокода. При оплате через шлюз код выпускается на вебхуке (пока юзер
 * на странице банка), поэтому коды показываем ещё и списком из /gift/my.
 */
export function GiftCard() {
  const t = useT();
  const [offers, setOffers] = useState<SubscriptionOffersResponse | null>(null);
  const [planCode, setPlanCode] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [method, setMethod] = useState<string>(BALANCE);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GiftResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [history, setHistory] = useState<GiftHistoryItem[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const pollsLeft = useRef(20);

  const loadHistory = useCallback(async () => {
    try {
      const r = await giftApi.my();
      setHistory(r.items);
    } catch {
      /* история не критична — форма работает и без неё */
    }
  }, []);

  useEffect(() => {
    subscriptionApi
      .offers()
      .then((d) => {
        setOffers(d);
        if (d.plans[0]) {
          setPlanCode(d.plans[0].public_code);
          setDays(d.plans[0].durations[0]?.days ?? null);
        }
      })
      .catch(() => setOffers(null));
    void loadHistory();
  }, [loadHistory]);

  const pending = history.some((g) => !g.issued);

  // Вернулись со страницы оплаты — код выпустит вебхук, дожидаемся его (2 минуты).
  useEffect(() => {
    if (!pending || pollsLeft.current <= 0) return;
    const id = setInterval(() => {
      if (pollsLeft.current <= 0) {
        clearInterval(id);
        return;
      }
      pollsLeft.current -= 1;
      void loadHistory();
    }, 6000);
    return () => clearInterval(id);
  }, [pending, loadHistory]);

  const plan = useMemo(() => offers?.plans.find((p) => p.public_code === planCode) ?? null, [offers, planCode]);
  // Подарок оплачивается только в рублях (та же проверка на бэкенде).
  const gateways = useMemo(() => (offers?.gateways ?? []).filter((g) => g.currency === "RUB"), [offers]);

  if (!offers || offers.plans.length === 0) return null;

  const submit = async () => {
    if (!planCode || !days) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await giftApi.create(planCode, days, method === BALANCE ? undefined : method);
      if (r.paid_by === "gateway") {
        // Кода в ответе нет — его выпустит вебхук; после возврата покажем в списке ниже.
        pollsLeft.current = 20;
        await loadHistory();
        if (r.payment_url) {
          window.location.href = r.payment_url;
          return;
        }
        setError(t("gift.noUrl"));
      } else {
        setResult(r);
        void loadHistory();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("gift.err"));
    } finally {
      setBusy(false);
    }
  };

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      setTimeout(() => setCopied((c) => (c === code ? null : c)), 1500);
    } catch {
      /* ignore */
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    pollsLeft.current = 20;
    await loadHistory();
    setRefreshing(false);
  };

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";
  const copyBtnCls = "inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-bg px-2.5 py-1.5 text-xs text-fg hover:bg-bg-raised";

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-4 sm:p-5">
      <div className="flex items-center gap-2">
        <Gift className="h-5 w-5 text-accent" />
        <h3 className="text-base font-bold text-fg">{t("gift.title")}</h3>
      </div>
      <p className="mt-1 text-xs text-fg-muted">{t("gift.subtitle")}</p>

      {result?.code ? (
        <div className="mt-4 rounded-xl border border-success/40 bg-success/10 p-4">
          <p className="text-sm text-fg-muted">
            {t("gift.created", { plan: result.plan_name, days: result.duration_days, price: result.price })}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="rounded-lg bg-bg px-3 py-1.5 text-base font-bold tracking-wider text-fg">{result.code}</code>
            <button type="button" onClick={() => copyCode(result.code!)} className={copyBtnCls}>
              {copied === result.code ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
              {copied === result.code ? t("gift.copied") : t("gift.copy")}
            </button>
          </div>
          <button type="button" onClick={() => setResult(null)} className="mt-3 text-xs font-medium text-accent hover:underline">
            {t("gift.again")}
          </button>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-fg-muted">{t("gift.plan")}</label>
              <select value={planCode} onChange={(e) => { setPlanCode(e.target.value); const p = offers.plans.find((x) => x.public_code === e.target.value); setDays(p?.durations[0]?.days ?? null); }} className={inputCls}>
                {offers.plans.map((p) => (
                  <option key={p.public_code} value={p.public_code}>{p.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-fg-muted">{t("gift.duration")}</label>
              <select value={days ?? ""} onChange={(e) => setDays(Number(e.target.value))} className={inputCls}>
                {plan?.durations.map((d) => (
                  <option key={d.days} value={d.days}>{t("gift.daysUnit", { days: d.days })}</option>
                ))}
              </select>
            </div>
            {gateways.length > 0 && (
              <div>
                <label className="mb-1 block text-xs text-fg-muted">{t("gift.payMethod")}</label>
                <select value={method} onChange={(e) => setMethod(e.target.value)} className={inputCls}>
                  <option value={BALANCE}>{t("gift.payBalance")}</option>
                  {gateways.map((g) => (
                    <option key={g.gateway_type} value={g.gateway_type}>{gatewayLabel(g.gateway_type)}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
          {error && <p className="mt-2 text-xs text-danger">{error}</p>}
          <button type="button" onClick={submit} disabled={busy || !planCode || !days} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Gift className="h-4 w-4" />}
            {method === BALANCE ? t("gift.submit") : t("gift.submitPay")}
          </button>
        </>
      )}

      {history.length > 0 && (
        <div className="mt-5 border-t border-border-subtle pt-4">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-fg">{t("gift.myTitle")}</h4>
            <button type="button" onClick={refresh} disabled={refreshing} className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg disabled:opacity-50">
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
              {t("gift.refresh")}
            </button>
          </div>
          <ul className="mt-2 space-y-2">
            {history.map((g) => (
              <li key={g.payment_id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border-subtle bg-bg px-3 py-2">
                <span className="text-xs text-fg-muted">
                  {g.plan_name} · {t("gift.daysUnit", { days: g.duration_days })} · {g.price} ₽
                </span>
                {g.code ? (
                  <span className="flex items-center gap-2">
                    <code className="text-xs font-bold tracking-wider text-fg">{g.code}</code>
                    <button type="button" onClick={() => copyCode(g.code!)} className={copyBtnCls}>
                      {copied === g.code ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
                      {copied === g.code ? t("gift.copied") : t("gift.copy")}
                    </button>
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
                    <Clock className="h-3.5 w-3.5" />
                    {t("gift.pending")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
