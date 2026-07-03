import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { clsx } from "clsx";
import { Check, ChevronDown, Sparkles } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatTrafficLimit } from "@/lib/format";
import type {
  PaymentGatewayType,
  PlanOfferResponse,
  SubscriptionOffersResponse,
} from "@/types/api";
import { ApiError } from "@/types/api";

const gatewayLabels: Record<string, string> = {
  YOOKASSA: "ЮKassa (карта)",
  YOOMONEY: "ЮMoney",
  PLATEGA: "Platega",
  CRYPTOMUS: "Криптовалюта",
  TELEGRAM_STARS: "Telegram Stars",
};

function priceFor(plan: PlanOfferResponse, days: number | null, gw: PaymentGatewayType | null) {
  if (days == null || gw == null) return null;
  const d = plan.durations.find((x) => x.days === days);
  if (!d) return null;
  return d.prices.find((p) => p.gateway_type === gw) ?? null;
}

function isPopular(plan: PlanOfferResponse): boolean {
  const hay = `${plan.name} ${plan.description ?? ""}`.toLowerCase();
  return hay.includes("хит") || hay.includes("популярн");
}

/**
 * Карточка тарифа — аккордеон: свёрнута показывает только имя/бейдж/цену,
 * по клику раскрывается со списком фич и кнопкой «Выбрать». Открыта может
 * быть только одна карточка одновременно (управляется родителем).
 */
function PlanCard({
  plan,
  days,
  gateway,
  busy,
  expanded,
  onToggle,
  onBuy,
}: {
  plan: PlanOfferResponse;
  days: number | null;
  gateway: PaymentGatewayType | null;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
  onBuy: () => void;
}) {
  const price = priceFor(plan, days, gateway);
  const popular = isPopular(plan);
  const perMonth =
    price && !price.is_free && days && days >= 30
      ? Math.round(Number(price.final_amount) / (days / 30))
      : null;

  const features = [
    plan.traffic_limit === 0 ? "Безлимитный трафик" : formatTrafficLimit(plan.traffic_limit),
    `До ${plan.device_limit} устройств`,
    "Все локации",
    "Подключение на любой платформе",
  ];

  return (
    <div
      className={clsx(
        "overflow-hidden rounded-2xl border transition-all duration-200",
        expanded
          ? "border-accent bg-accent-subtle/40 shadow-[0_18px_50px_-20px_var(--accent-glow)]"
          : "border-[var(--border-subtle)] bg-bg-raised hover:border-[var(--accent)]/50",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left"
      >
        {popular && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white">
            <Sparkles className="h-3 w-3" /> Хит
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[15px] font-bold leading-tight text-fg">{plan.name}</h3>
          {plan.description && (
            <p className="mt-0.5 truncate text-xs text-fg-subtle">{plan.description}</p>
          )}
        </div>
        <div className="shrink-0 text-right">
          {price ? (
            price.is_free ? (
              <span className="text-base font-extrabold text-success">Бесплатно</span>
            ) : (
              <span className="text-base font-extrabold text-fg">
                {price.final_amount} {price.currency_symbol}
              </span>
            )
          ) : (
            <span className="text-sm text-fg-subtle">—</span>
          )}
        </div>
        <ChevronDown
          className={clsx(
            "h-4 w-4 shrink-0 text-fg-muted transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded && (
        <div className="border-t border-[var(--border-subtle)] px-4 pb-4 pt-3.5">
          <p className="text-xs text-fg-subtle">
            {days ? `за ${days} дн.` : "выберите срок"}
            {perMonth ? ` · ≈ ${perMonth} ${price?.currency_symbol}/мес` : ""}
            {price && !price.is_free && price.discount_percent > 0 && (
              <span className="ml-1.5 text-fg-subtle line-through">{price.original_amount}</span>
            )}
          </p>

          {/* Фичи */}
          <ul className="mt-3 flex flex-col gap-2.5">
            {features.map((f) => (
              <li key={f} className="flex items-center gap-2 text-sm text-fg-muted">
                <span className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent">
                  <Check className="h-3 w-3" strokeWidth={3} />
                </span>
                {f}
              </li>
            ))}
          </ul>

          <button
            onClick={onBuy}
            disabled={busy || !price}
            className="btn-gradient mt-4 inline-flex h-11 w-full items-center justify-center rounded-xl px-5 text-sm font-semibold text-white transition-all active:scale-[0.98] disabled:opacity-60"
          >
            {busy ? "Переход к оплате…" : "Выбрать"}
          </button>
        </div>
      )}
    </div>
  );
}

export default function BillingPage() {
  const [offers, setOffers] = useState<SubscriptionOffersResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedDays, setSelectedDays] = useState<number | null>(null);
  const [selectedGateway, setSelectedGateway] = useState<PaymentGatewayType | null>(null);
  const [purchasingCode, setPurchasingCode] = useState<string | null>(null);
  const [purchaseError, setPurchaseError] = useState<string | null>(null);
  // Аккордеон: раскрыта не больше одной карточки тарифа одновременно.
  const [expandedCode, setExpandedCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await subscriptionApi.offers();
      setOffers(data);
      if (data.gateways.length > 0) setSelectedGateway(data.gateways[0]!.gateway_type);
      const firstDuration = data.plans[0]?.durations[0]?.days ?? null;
      setSelectedDays(firstDuration);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось загрузить тарифы");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Доступные сроки (объединение по всем тарифам).
  const termDays = useMemo(() => {
    const set = new Set<number>();
    offers?.plans.forEach((p) => p.durations.forEach((d) => set.add(d.days)));
    return Array.from(set).sort((a, b) => a - b);
  }, [offers]);

  const handlePurchase = async (plan: PlanOfferResponse) => {
    if (!selectedDays || !selectedGateway) return;
    setPurchasingCode(plan.public_code);
    setPurchaseError(null);
    try {
      const isRenew = plan.recommended_purchase_type === "RENEW";
      const result = isRenew
        ? await subscriptionApi.extend({ duration_days: selectedDays, gateway_type: selectedGateway })
        : await subscriptionApi.purchase({
            plan_code: plan.public_code,
            duration_days: selectedDays,
            gateway_type: selectedGateway,
          });

      if (result.is_free) {
        window.location.href = "/";
      } else if (result.payment_url) {
        window.location.href = result.payment_url;
      }
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "";
      const isEmailError =
        detail.toLowerCase().includes("email") || detail.toLowerCase().includes("verified");
      setPurchaseError(isEmailError ? "__email__" : detail || "Не удалось создать оплату");
    } finally {
      setPurchasingCode(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">Тарифы</h1>
        <div className="mx-auto flex w-full max-w-xl flex-col gap-2.5">
          <Skeleton className="h-14 w-full rounded-2xl" />
          <Skeleton className="h-14 w-full rounded-2xl" />
          <Skeleton className="h-14 w-full rounded-2xl" />
          <Skeleton className="h-14 w-full rounded-2xl" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">Тарифы</h1>
        <p className="text-sm text-danger">{error}</p>
      </div>
    );
  }

  if (!offers || offers.plans.length === 0) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">Тарифы</h1>
        <p className="text-sm text-fg-subtle">Сейчас нет доступных тарифов.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-fg sm:text-[28px]">Тарифы</h1>
        <p className="mt-1.5 text-sm text-fg-muted">
          Выберите срок и тариф — оплата картой, СБП или криптовалютой
        </p>
      </div>

      {/* Срок — сегментированный переключатель (меняет цены на всех карточках) */}
      {termDays.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {termDays.map((d) => (
            <button
              key={d}
              onClick={() => setSelectedDays(d)}
              className={clsx(
                "rounded-xl border px-4 py-2 text-sm font-medium transition-all",
                selectedDays === d
                  ? "border-accent bg-accent-subtle text-accent"
                  : "border-border-subtle bg-bg-subtle text-fg-muted hover:border-border",
              )}
            >
              {d} дн.
            </button>
          ))}
        </div>
      )}

      {/* Способ оплаты */}
      {offers.gateways.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">Оплата:</span>
          {offers.gateways.map((gw) => (
            <button
              key={gw.gateway_type}
              onClick={() => setSelectedGateway(gw.gateway_type)}
              className={clsx(
                "rounded-lg border px-3 py-1.5 text-xs font-medium transition-all",
                selectedGateway === gw.gateway_type
                  ? "border-accent bg-accent-subtle text-accent"
                  : "border-border-subtle bg-bg-subtle text-fg-muted hover:border-border",
              )}
            >
              {gatewayLabels[gw.gateway_type] || gw.gateway_type}
            </button>
          ))}
        </div>
      )}

      {purchaseError && purchaseError !== "__email__" && (
        <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-3">
          <p className="text-sm font-medium text-danger">
            {purchaseError === "Unknown error" || purchaseError === "Internal Server Error"
              ? "Ошибка соединения с платёжной системой. Попробуйте ещё раз или выберите другой способ."
              : purchaseError}
          </p>
        </div>
      )}
      {purchaseError === "__email__" && (
        <div className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-fg">
          Сначала подтвердите email.{" "}
          <Link to="/settings" className="font-medium text-accent underline-offset-2 hover:underline">
            Перейти в настройки
          </Link>
        </div>
      )}

      {/* Карточки тарифов — аккордеон, раскрывается по клику */}
      <div className="mx-auto flex w-full max-w-xl flex-col gap-2.5">
        {offers.plans.map((plan) => (
          <PlanCard
            key={plan.public_code}
            plan={plan}
            days={selectedDays}
            gateway={selectedGateway}
            busy={purchasingCode === plan.public_code}
            expanded={expandedCode === plan.public_code}
            onToggle={() =>
              setExpandedCode((c) => (c === plan.public_code ? null : plan.public_code))
            }
            onBuy={() => handlePurchase(plan)}
          />
        ))}
      </div>
    </div>
  );
}
