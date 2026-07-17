import { useEffect, useMemo, useState } from "react";
import { Gift, Loader2, Copy, Check } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { giftApi, type GiftResult } from "@/api/gift";
import type { SubscriptionOffersResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

/**
 * «Подарить подписку»: даритель выбирает тариф+срок, цена списывается с его ₽-баланса,
 * генерируется одноразовый код. Получатель активирует код обычным вводом промокода.
 */
export function GiftCard() {
  const t = useT();
  const [offers, setOffers] = useState<SubscriptionOffersResponse | null>(null);
  const [planCode, setPlanCode] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GiftResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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
  }, []);

  const plan = useMemo(() => offers?.plans.find((p) => p.public_code === planCode) ?? null, [offers, planCode]);

  if (!offers || offers.plans.length === 0) return null;

  const submit = async () => {
    if (!planCode || !days) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await giftApi.create(planCode, days);
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("gift.err"));
    } finally {
      setBusy(false);
    }
  };

  const copyCode = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  const inputCls = "w-full rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent";

  return (
    <section className="rounded-2xl border border-border-subtle bg-bg-subtle p-4 sm:p-5">
      <div className="flex items-center gap-2">
        <Gift className="h-5 w-5 text-accent" />
        <h3 className="text-base font-bold text-fg">{t("gift.title")}</h3>
      </div>
      <p className="mt-1 text-xs text-fg-muted">{t("gift.subtitle")}</p>

      {result ? (
        <div className="mt-4 rounded-xl border border-success/40 bg-success/10 p-4">
          <p className="text-sm text-fg-muted">
            {t("gift.created", { plan: result.plan_name, days: result.duration_days, price: result.price })}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="rounded-lg bg-bg px-3 py-1.5 text-base font-bold tracking-wider text-fg">{result.code}</code>
            <button type="button" onClick={copyCode} className="inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-bg px-2.5 py-1.5 text-xs text-fg hover:bg-bg-raised">
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? t("gift.copied") : t("gift.copy")}
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
          </div>
          {error && <p className="mt-2 text-xs text-danger">{error}</p>}
          <button type="button" onClick={submit} disabled={busy || !planCode || !days} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg hover:bg-accent/90 disabled:opacity-50">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Gift className="h-4 w-4" />}
            {t("gift.submit")}
          </button>
        </>
      )}
    </section>
  );
}
