import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { clsx } from "clsx";
import { AlertTriangle, Check, ChevronDown, Info, Sparkles, Wallet } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { balanceApi } from "@/api/balance";
import { Skeleton } from "@/components/ui/Skeleton";
import { PromocodeCard } from "@/components/PromocodeCard";
import { TrialDiscountBanner } from "@/components/TrialDiscountBanner";
import { RenewalDiscountBanner } from "@/components/RenewalBanner";
import { formatDate, formatTrafficLimit } from "@/lib/format";
import { changeExtraNote, renewExtraUntil, type ChangeExtraNote } from "@/lib/extraDevice";
import { onReturnFromPayment, openPayment } from "@/lib/payment";
import { changeTrafficNote, offerView } from "@/lib/extraTraffic";
import { ExtraTrafficHeadline, ExtraTrafficOffer } from "@/components/ExtraTrafficOffer";
import {
  changeTerms,
  currencyOf,
  needsConfirm,
  paymentsBlocked,
  readBillingPreselect,
  type ChangeTerms,
} from "@/lib/planChange";
import type {
  ExtraTrafficResponse,
  PaymentGatewayType,
  PlanOfferResponse,
  SubscriptionOffersResponse,
} from "@/types/api";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";
import { useBranding } from "@/contexts/BrandingContext";

const gatewayLabels: Record<string, string> = {
  YOOKASSA: "billing.gwYookassa",
  YOOMONEY: "billing.gwYoomoney",
  PLATEGA: "billing.gwPlatega",
  CRYPTOMUS: "billing.gwCryptomus",
  TELEGRAM_STARS: "billing.gwStars",
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

type ConfirmAction = "gateway" | "balance";

/**
 * Карточка тарифа — аккордеон: свёрнута показывает только имя/бейдж/цену,
 * по клику раскрывается со списком фич и кнопкой «Выбрать». Открыта может
 * быть только одна карточка одновременно (управляется родителем).
 *
 * Над кнопками — что будет с остатком текущей подписки (`terms`): перенос по цене
 * дня спокойной строкой, потеря — предупреждением. Если что-то пропадает
 * (`needsConfirm`), первый клик по оплате только спрашивает подтверждение
 * (`confirmAction`) — платит второй.
 */
function PlanCard({
  plan,
  days,
  gateway,
  busy,
  balance,
  expanded,
  terms,
  extraNote,
  trafficNote,
  renewExtraUntil,
  confirmAction,
  onToggle,
  onBuy,
  onBuyBalance,
  onCancelConfirm,
}: {
  plan: PlanOfferResponse;
  days: number | null;
  gateway: PaymentGatewayType | null;
  busy: boolean;
  balance: number;
  expanded: boolean;
  terms: ChangeTerms;
  /** Что станет с докупленными местами (null — бэкенд про них молчит). */
  extraNote: ChangeExtraNote;
  /** Сколько докупленных ГБ сгорит при смене тарифа. null — молчим. */
  trafficNote: { gb: number } | null;
  renewExtraUntil: string | null;
  confirmAction: ConfirmAction | null;
  onToggle: () => void;
  onBuy: () => void;
  onBuyBalance: () => void;
  onCancelConfirm: () => void;
}) {
  const t = useT();
  const { can } = useBranding();
  const price = priceFor(plan, days, gateway);
  // Покупка картой и списание с баланса — разные возможности бэкенда: под чужим
  // ботом может не быть ни той, ни другой, и тогда кнопку показывать нельзя.
  const canBuy = can("purchase");
  const canPayBalance =
    can("pay_with_balance") &&
    !!price && !price.is_free && price.currency === "RUB" && balance >= Number(price.final_amount);
  const popular = isPopular(plan);
  const perMonth =
    price && !price.is_free && days && days >= 30
      ? Math.round(Number(price.final_amount) / (days / 30))
      : null;

  const features = [
    plan.traffic_limit === 0 ? t("billing.unlimitedTraffic") : formatTrafficLimit(plan.traffic_limit),
    t("billing.upToDevices", { n: plan.device_limit }),
    t("billing.allLocations"),
    t("billing.anyPlatform"),
  ];

  return (
    <div
      id={`plan-${plan.public_code}`}
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
            <Sparkles className="h-3 w-3" /> {t("billing.hit")}
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
              <span className="text-base font-extrabold text-success">{t("billing.free")}</span>
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
            {days ? t("billing.forDays", { days }) : t("billing.chooseTerm")}
            {perMonth ? " · " + t("billing.perMonth", { amount: perMonth, sym: price?.currency_symbol ?? "" }) : ""}
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

          {/* До клика: что будет с остатком, видно раньше, чем кнопка оплаты. Перенос без
              потерь — нейтрально (это не беда), потеря — предупреждением. */}
          {terms && !confirmAction && (canBuy || canPayBalance) && (
            needsConfirm(terms) ? (
              <p className="mt-4 flex gap-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-fg">
                <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-warning" />
                <span>
                  {terms.kind === "days"
                    ? t("billing.changeWarn", { days: terms.days })
                    : terms.kind === "carry"
                      ? terms.lostReason === "cap"
                        ? t("billing.changeCarryLostCap", { bonus: terms.bonus, lost: terms.lost })
                        : t("billing.changeCarryLost", { bonus: terms.bonus, lost: terms.lost })
                      : t("billing.changeWarnLifetime")}
                </span>
              </p>
            ) : terms.kind === "carry" ? (
              <p className="mt-4 flex gap-2 rounded-xl border border-[var(--border-subtle)] bg-bg-subtle px-3 py-2.5 text-xs text-fg-muted">
                <Info className="mt-px h-4 w-4 shrink-0 text-accent" />
                <span>
                  {terms.samePlan
                    ? t("billing.changeCarrySamePlan", { left: terms.left })
                    : terms.bonus > 0
                      ? t("billing.changeCarry", { left: terms.left, bonus: terms.bonus })
                      : t("billing.changeCarrySmall", { left: terms.left })}
                </span>
              </p>
            ) : null
          )}

          {/* Докупленные места: ёмкость при смене тарифа сгорает всегда, разница лишь
              в том, пересчитывается ли их стоимость в дни. Продление места не
              продлевает — об этом честно предупреждаем на RENEW. */}
          {!confirmAction && (canBuy || canPayBalance) && extraNote && (
            <p
              className={
                extraNote.kind === "carry"
                  ? "mt-2 flex gap-2 rounded-xl border border-[var(--border-subtle)] bg-bg-subtle px-3 py-2.5 text-xs text-fg-muted"
                  : "mt-2 flex gap-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-fg"
              }
            >
              {extraNote.kind === "carry" ? (
                <Info className="mt-px h-4 w-4 shrink-0 text-accent" />
              ) : (
                <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-warning" />
              )}
              <span>
                {extraNote.kind === "carry"
                  ? t("billing.changeExtra", { n: extraNote.count, limit: extraNote.limit })
                  : t("billing.changeExtraLost", { n: extraNote.count, limit: extraNote.limit })}
              </span>
            </p>
          )}
          {/* Докупленные ГБ при смене тарифа СГОРАЮТ (решение владельца Р-1:
              стоимость днями не переносим). Новый тариф даёт свой объём и обнуляет
              расход, так что человек не в убытке, — но узнать он должен ДО оплаты. */}
          {!confirmAction && (canBuy || canPayBalance) && trafficNote && (
            <p className="mt-2 flex gap-2 rounded-xl border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-fg">
              <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-warning" />
              <span>{t("extraTraffic.burnOnChange", { gb: trafficNote.gb })}</span>
            </p>
          )}
          {!confirmAction && (canBuy || canPayBalance) && renewExtraUntil && (
            <p className="mt-2 flex gap-2 rounded-xl border border-[var(--border-subtle)] bg-bg-subtle px-3 py-2.5 text-xs text-fg-muted">
              <Info className="mt-px h-4 w-4 shrink-0 text-accent" />
              <span>{t("billing.renewExtra", { date: formatDate(renewExtraUntil) })}</span>
            </p>
          )}

          {/* Подтверждение прямо в карточке, а не window.confirm: оплата идёт и из
              Telegram Mini App, где системный диалог ведёт себя по-своему. Пока оно
              открыто, обычные кнопки оплаты спрятаны — «да» здесь единственный путь. */}
          {confirmAction && (
            <div className="mt-4 rounded-xl border border-warning/40 bg-warning/10 p-3">
              <p className="flex gap-2 text-sm text-fg">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <span>
                  {terms?.kind === "days"
                    ? t("billing.changeConfirm", { days: terms.days })
                    : terms?.kind === "carry"
                      ? t("billing.changeCarryConfirm", { lost: terms.lost, bonus: terms.bonus })
                      : t("billing.changeWarnLifetime")}
                </span>
              </p>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <button
                  onClick={confirmAction === "balance" ? onBuyBalance : onBuy}
                  disabled={busy}
                  className="btn-gradient inline-flex h-11 flex-1 items-center justify-center rounded-xl px-5 text-sm font-semibold text-white transition-all active:scale-[0.98] disabled:opacity-60"
                >
                  {busy ? t("billing.goingToPay") : t("billing.changeConfirmYes")}
                </button>
                <button
                  onClick={onCancelConfirm}
                  disabled={busy}
                  className="inline-flex h-11 flex-1 items-center justify-center rounded-xl border border-border-subtle bg-bg-subtle px-5 text-sm font-medium text-fg-muted transition-all hover:border-border disabled:opacity-60"
                >
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          )}

          {canBuy && !confirmAction && <button
            onClick={onBuy}
            disabled={busy || !price}
            className="btn-gradient mt-4 inline-flex h-11 w-full items-center justify-center rounded-xl px-5 text-sm font-semibold text-white transition-all active:scale-[0.98] disabled:opacity-60"
          >
            {busy ? t("billing.goingToPay") : t("billing.select")}
          </button>}

          {canPayBalance && !confirmAction && (
            <button
              onClick={onBuyBalance}
              disabled={busy}
              className="mt-2 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-accent/30 bg-gradient-to-r from-[var(--accent)]/15 to-[var(--accent-2)]/15 px-5 text-sm font-semibold text-accent transition-all hover:border-accent/50 hover:from-[var(--accent)]/25 hover:to-[var(--accent-2)]/25 hover:-translate-y-px active:translate-y-0 active:scale-[0.98] disabled:opacity-60"
            >
              <Wallet className="h-4 w-4" />
              {t("billing.payBalance", { amount: price!.final_amount })}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function BillingPage() {
  const t = useT();
  const { appearance, can } = useBranding();
  // Тех-работы: оплата ограничена (галка в оформлении).
  const payBlocked = paymentsBlocked(appearance);
  // `?plan=&days=` — ссылка из блока «Нужно больше устройств?»: раскрыть тариф и
  // выбрать срок. Только предвыбор, оплату ссылка не запускает.
  const [searchParams] = useSearchParams();
  // Счёт открыт во внешнем окне (мини-апп): ждём возвращения человека, чтобы
  // перечитать подписку — редирект платёжки сюда не доезжает.
  const [paymentOpened, setPaymentOpened] = useState(false);
  const [offers, setOffers] = useState<SubscriptionOffersResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedDays, setSelectedDays] = useState<number | null>(null);
  const [selectedGateway, setSelectedGateway] = useState<PaymentGatewayType | null>(null);
  const [purchasingCode, setPurchasingCode] = useState<string | null>(null);
  const [purchaseError, setPurchaseError] = useState<string | null>(null);
  const [balance, setBalance] = useState(0);
  // Аккордеон: раскрыта не больше одной карточки тарифа одновременно.
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  // Ждёт подтверждения смены тарифа: какая карточка и какой кнопкой платим.
  const [confirm, setConfirm] = useState<{ code: string; action: ConfirmAction } | null>(null);
  // К какой карточке прокрутить после загрузки (один раз, из ссылки).
  const scrollToCode = useRef<string | null>(null);
  // Докупка трафика. На «Оплате» показываем БЕЗ порога расхода: сюда человек пришёл
  // сам разбираться с тарифом, и прятать от него вариант дешевле апгрейда незачем.
  const [extraTraffic, setExtraTraffic] = useState<ExtraTrafficResponse | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await subscriptionApi.offers();
      setOffers(data);
      if (data.gateways.length > 0) setSelectedGateway(data.gateways[0]!.gateway_type);
      const firstDuration = data.plans[0]?.durations[0]?.days ?? null;
      const preselect = readBillingPreselect(searchParams, data);
      setSelectedDays(preselect.days ?? firstDuration);
      if (preselect.code) {
        setExpandedCode(preselect.code);
        scrollToCode.current = preselect.code;
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("billing.errLoad"));
    } finally {
      setIsLoading(false);
    }
    // t нужен лишь для фолбэка ошибки — лоадер не должен перезапускаться на смене языка
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    balanceApi.get().then((b) => setBalance(b.balance)).catch(() => {});
  }, []);

  // ЗАВИСИМОСТЬ — БУЛЕВО ЗНАЧЕНИЕ, А НЕ ФУНКЦИЯ `can`. Её identity меняется на каждый
  // рендер (и в кабинете, и в подделке теста), а эффект, который зависит от identity
  // и сам вызывает setState, — это бесконечный цикл рендеров.
  const trafficCapable = appearance != null && can("extra_traffic");
  const [trafficNonce, setTrafficNonce] = useState(0);

  useEffect(() => {
    if (!trafficCapable) return;
    // Ответ, пришедший после ухода со страницы или после нового запроса, состояние
    // не перезаписывает: иначе карточка мигала бы устаревшими числами.
    let alive = true;
    subscriptionApi
      .extraTraffic()
      .then((d) => {
        if (alive) setExtraTraffic(d);
      })
      .catch(() => {
        if (alive) setExtraTraffic(null);
      });
    return () => {
      alive = false;
    };
  }, [trafficCapable, trafficNonce]);

  const loadExtraTraffic = useCallback(() => setTrafficNonce((n) => n + 1), []);

  // Вернулись из внешнего окна оплаты — перечитываем витрину и предложение:
  // подписка могла уже стать оплаченной, а страница об этом не узнала бы.
  useEffect(() => {
    if (!paymentOpened) return;
    return onReturnFromPayment(() => {
      void load();
      loadExtraTraffic();
    });
  }, [paymentOpened, load, loadExtraTraffic]);

  useEffect(() => {
    load();
  }, [load]);

  // Прокрутка к тарифу из ссылки — когда карточки уже на странице.
  useEffect(() => {
    const code = scrollToCode.current;
    if (isLoading || !offers || !code) return;
    scrollToCode.current = null;
    const el = document.getElementById(`plan-${code}`);
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [isLoading, offers]);

  // Подтверждение относится к конкретной цене: сменили срок, шлюз или карточку —
  // спрашиваем заново, иначе «да» ушло бы за другую сумму.
  useEffect(() => {
    setConfirm(null);
  }, [selectedDays, selectedGateway, expandedCode]);

  // Предложение докупить трафик: null — выключено, нельзя или платить нечем.
  const trafficOffer = offerView(extraTraffic);

  /** Что будет с остатком при покупке тарифа на выбранный срок выбранным шлюзом. */
  const termsFor = (plan: PlanOfferResponse): ChangeTerms =>
    offers ? changeTerms(offers, plan, selectedDays, currencyOf(offers, selectedGateway)) : null;

  /**
   * Первый клик по оплате тарифа, при смене на который что-то пропадёт, только
   * спрашивает. Перенос без потерь платится с первого клика.
   * true — можно платить; false — показали подтверждение и ждём второго клика.
   */
  const confirmed = (plan: PlanOfferResponse, action: ConfirmAction): boolean => {
    // Докупленные места, чья стоимость НЕ пересчитывается в дни, — тоже потеря:
    // спрашиваем, даже если сами дни переносятся целиком.
    const extraLost =
      plan.recommended_purchase_type === "CHANGE" &&
      changeExtraNote(offers, plan.device_limit)?.kind === "lost";
    if (
      needsConfirm(termsFor(plan), extraLost) &&
      !(confirm?.code === plan.public_code && confirm.action === action)
    ) {
      setConfirm({ code: plan.public_code, action });
      return false;
    }
    setConfirm(null);
    return true;
  };

  // Доступные сроки (объединение по всем тарифам).
  const termDays = useMemo(() => {
    const set = new Set<number>();
    offers?.plans.forEach((p) => p.durations.forEach((d) => set.add(d.days)));
    return Array.from(set).sort((a, b) => a - b);
  }, [offers]);

  const handlePurchase = async (plan: PlanOfferResponse) => {
    if (payBlocked) return;
    if (!selectedDays || !selectedGateway) return;
    if (!confirmed(plan, "gateway")) return;
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
        // В мини-аппе счёт открываем наружу: если увести сам WebView на платёжку,
        // «назад» в Telegram возвращать уже некуда.
        if (openPayment(result.payment_url)) {
          setPaymentOpened(true);
        }
      }
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "";
      const isEmailError =
        detail.toLowerCase().includes("email") || detail.toLowerCase().includes("verified");
      setPurchaseError(isEmailError ? "__email__" : detail || t("billing.errPay"));
    } finally {
      setPurchasingCode(null);
    }
  };

  const handleBuyBalance = async (plan: PlanOfferResponse) => {
    if (payBlocked) return;
    if (!selectedDays || !selectedGateway) return;
    if (!confirmed(plan, "balance")) return;
    setPurchasingCode(plan.public_code);
    setPurchaseError(null);
    try {
      await subscriptionApi.payWithBalance({
        plan_code: plan.public_code,
        duration_days: selectedDays,
        gateway_type: selectedGateway,
      });
      window.location.href = "/";
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "";
      const isEmailError =
        detail.toLowerCase().includes("email") || detail.toLowerCase().includes("verified");
      setPurchaseError(isEmailError ? "__email__" : detail || t("billing.errPay"));
    } finally {
      setPurchasingCode(null);
    }
  };

  if (payBlocked) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("billing.title")}</h1>
        <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-4 text-sm text-fg-muted">
          {appearance?.maintenance_message?.trim() || t("maintenance.paymentsClosed")}
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("billing.title")}</h1>
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
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("billing.title")}</h1>
        <p className="text-sm text-danger">{error}</p>
      </div>
    );
  }

  if (!offers || offers.plans.length === 0) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">{t("billing.title")}</h1>
        <p className="text-sm text-fg-subtle">{t("billing.noPlans")}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-fg sm:text-[28px]">{t("billing.title")}</h1>
        <p className="mt-1.5 text-sm text-fg-muted">
          {t("billing.subtitle")}
        </p>
      </div>

      {/* Скидка на первую покупку триальщику — баннер-таймер (если активна) */}
      <TrialDiscountBanner />
      {/* Скидка на продление до окончания подписки (если выдана) */}
      <RenewalDiscountBanner />

      {/* Промокод — можно активировать бонус, не покупая тариф */}
      <PromocodeCard />

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
              {t("billing.termDays", { d })}
            </button>
          ))}
        </div>
      )}

      {/* Способ оплаты */}
      {offers.gateways.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-fg-subtle">{t("billing.paymentLabel")}</span>
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
              {t(gatewayLabels[gw.gateway_type] ?? gw.gateway_type)}
            </button>
          ))}
        </div>
      )}

      {purchaseError && purchaseError !== "__email__" && (
        <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-3">
          <p className="text-sm font-medium text-danger">
            {purchaseError === "Unknown error" || purchaseError === "Internal Server Error"
              ? t("billing.errConnection")
              : purchaseError}
          </p>
        </div>
      )}
      {/* Счёт открыт во внешнем окне (мини-апп): человек должен понимать, что
          страница его дождётся и обновится сама. */}
      {paymentOpened && (
        <div className="rounded-xl border border-accent/30 bg-bg-subtle px-4 py-3 text-sm text-fg">
          {t("payment.openedExternally")}
        </div>
      )}
      {purchaseError === "__email__" && (
        <div className="rounded-xl border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-fg">
          {t("billing.confirmEmail")}{" "}
          <Link to="/settings" className="font-medium text-accent underline-offset-2 hover:underline">
            {t("billing.goToSettings")}
          </Link>
        </div>
      )}

      {/* Докупить трафик к текущему периоду — дешевле перехода на тариф побольше,
          но и живёт только до ближайшего обновления трафика. Карточка сама себя
          прячет, если купить нельзя или платить нечем. */}
      {trafficOffer && extraTraffic && (
        <div className="mx-auto w-full max-w-xl rounded-2xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-4">
          <ExtraTrafficHeadline data={extraTraffic} />
          <ExtraTrafficOffer
            data={extraTraffic}
            offer={trafficOffer}
            // Метка в адресе значит «человек уже нажал докупить»: шаг оплаты
            // открываем сразу и подводим к нему экран, а не заставляем искать
            // карточку под ползунком расхода. `extra_traffic=1` шлёт бот (кнопка
            // меню и письмо «трафик закончился»), `buy=traffic` — запасное
            // написание для ссылок, сделанных руками.
            autoOpen={
              searchParams.get("extra_traffic") === "1" || searchParams.get("buy") === "traffic"
            }
            onChanged={() => {
              load();
              loadExtraTraffic();
              balanceApi.get().then((b) => setBalance(b.balance)).catch(() => {});
            }}
          />
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
            balance={balance}
            expanded={expandedCode === plan.public_code}
            terms={termsFor(plan)}
            extraNote={
              plan.recommended_purchase_type === "CHANGE"
                ? changeExtraNote(offers, plan.device_limit)
                : null
            }
            trafficNote={
              plan.recommended_purchase_type === "CHANGE" ? changeTrafficNote(offers) : null
            }
            renewExtraUntil={
              plan.recommended_purchase_type === "RENEW" ? renewExtraUntil(offers) : null
            }
            confirmAction={confirm?.code === plan.public_code ? confirm.action : null}
            onToggle={() =>
              setExpandedCode((c) => (c === plan.public_code ? null : plan.public_code))
            }
            onBuy={() => handlePurchase(plan)}
            onBuyBalance={() => handleBuyBalance(plan)}
            onCancelConfirm={() => setConfirm(null)}
          />
        ))}
      </div>
    </div>
  );
}
