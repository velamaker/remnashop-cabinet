import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Layers, MonitorSmartphone, X } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";
import {
  deviceLimitState,
  homeAllows,
  homePrecheck,
  pickDeviceUpgrade,
} from "@/lib/deviceUpsell";
import { newOffer, payOptions, suggestsBiggerPlan } from "@/lib/extraDevice";
import { ExtraDeviceHeadline, ExtraDeviceOffer } from "./ExtraDeviceOffer";
import { billingHref, changeTerms, paymentsBlocked } from "@/lib/planChange";
import type {
  DevicesResponse,
  ExtraDeviceResponse,
  SubscriptionInfoResponse,
  SubscriptionOffersResponse,
} from "@/types/api";

const HIDE_KEY = "device_upsell_hidden_until";
const HIDE_MS = 7 * 86400_000;

// Хранилище может бросать (приватный режим, запрет сайта) — тогда блок просто
// не запоминает крестик, но рисуется как обычно.
function hiddenNow(): boolean {
  try {
    return Number(localStorage.getItem(HIDE_KEY) ?? 0) > Date.now();
  } catch {
    return false;
  }
}

function rememberHidden() {
  try {
    localStorage.setItem(HIDE_KEY, String(Date.now() + HIDE_MS));
  } catch {
    /* не запомнили — не беда */
  }
}

/**
 * «Нужно больше устройств?» — блок при заполненном лимите устройств.
 *
 * ПОРЯДОК ПРЕДЛОЖЕНИЙ: сначала бесплатное («освободите места»), потом дешёвое
 * («+1 устройство за X ₽ до конца срока»), и только потом дорогое (тариф побольше).
 * Докупка встаёт между ними и на Главной не требует предпроверки потери дней: у неё
 * нечему пропадать, она не меняет тариф.
 *
 * - «Устройства» (variant="devices"): при упоре всегда — сюда панель сама отправляет
 *   тех, у кого новое устройство не подключилось. Если места заняли дубли одного
 *   аппарата и уборка снимает упор, молчим: там уже есть подсказка про дубли.
 * - Главная (variant="home"): тариф — только когда при смене пропадёт не больше недели
 *   (остаток переносится по цене дня; иначе блок звал бы платить и тут же
 *   отговаривал); при дублях — карточка «освободите места». Крестик прячет блок на неделю.
 *
 * Запросов лишних не делает: ждёт оформление (на чужом бэкенде возможность может
 * быть выключена), ходит за витриной только при упоре и только один раз, а на
 * Главной — лишь если срок уже подходит к концу. Любая ошибка — тишина.
 */
export function DeviceUpsellCard({
  variant,
  subscription,
  devices,
  onChanged,
  onOfferShown,
}: {
  variant: "home" | "devices";
  subscription: SubscriptionInfoResponse | null;
  devices: DevicesResponse | null;
  /** Место докуплено: перечитать устройства (лимит вырос) и предложение. */
  onChanged?: () => void;
  /** Показывает ли карточка кнопку докупки — чтобы панель «Устройств» её не дублировала. */
  onOfferShown?: (shown: boolean) => void;
}) {
  const t = useT();
  const { appearance, can } = useBranding();
  const [hidden, setHidden] = useState(() => variant === "home" && hiddenNow());
  const [offers, setOffers] = useState<SubscriptionOffersResponse | null>(null);
  const [extra, setExtra] = useState<ExtraDeviceResponse | null>(null);
  const requested = useRef(false);
  const extraRequested = useRef(false);

  // Без оформления не знаем, умеет ли бэкенд этот блок: `can` на пустом оформлении
  // отвечает «да», и поверх «Бедолаги» первый заход без кэша сходил бы за витриной.
  const allowed =
    appearance != null &&
    can("device_upsell") &&
    can("purchase") &&
    appearance.device_upsell_enabled !== false &&
    !paymentsBlocked(appearance);

  const state = useMemo(
    () => (allowed && !hidden ? deviceLimitState(subscription, devices) : { kind: "ok" as const }),
    [allowed, hidden, subscription, devices],
  );

  // Докупка отвечает «место уже покупали» или «больше нельзя» — тариф побольше
  // становится единственным выходом, и ради него витрину стоит спросить даже на
  // Главной, где обычно ждём конца срока (решение владельца: второй раз место не
  // предлагаем, ведём на тариф).
  const wantOffers =
    state.kind === "full" &&
    !!subscription &&
    (variant === "devices" || homePrecheck(subscription) || suggestsBiggerPlan(extra));

  useEffect(() => {
    if (!wantOffers || requested.current) return;
    requested.current = true;
    subscriptionApi
      .offers()
      .then(setOffers)
      .catch(() => {
        /* тихо: блок необязательный (501 у чужого бэкенда, сбой сети) */
      });
  }, [wantOffers]);

  // Докупка — отдельный, дешёвый запрос (три SELECT, без панели) и БЕЗ предпроверки
  // потери дней: тариф она не меняет, терять нечего.
  const wantExtra = state.kind === "full" && !!subscription && can("extra_device");

  const loadExtra = () => {
    subscriptionApi
      .extraDevice()
      .then(setExtra)
      .catch(() => {
        /* тихо: докупки может не быть (старый бот, чужой бэкенд, сбой сети) */
      });
  };

  useEffect(() => {
    if (!wantExtra || extraRequested.current) return;
    extraRequested.current = true;
    loadExtra();
  }, [wantExtra]);

  const extraOffer = useMemo(() => (wantExtra ? newOffer(extra) : null), [wantExtra, extra]);
  const extraPayable =
    extraOffer != null && extra != null && payOptions(extra, extraOffer.amount).options.length > 0;

  useEffect(() => {
    onOfferShown?.(extraPayable);
  }, [extraPayable, onOfferShown]);

  const upgrade = useMemo(
    () =>
      offers && subscription && devices
        ? pickDeviceUpgrade(offers, {
            maxDevices: devices.max_count,
            trafficLimit: subscription.traffic_limit,
            durationDays: subscription.plan_duration_days,
          })
        : null,
    [offers, subscription, devices],
  );

  const hide = () => {
    rememberHidden();
    setHidden(true);
  };

  const closeButton =
    variant === "home" ? (
      <button
        type="button"
        onClick={hide}
        aria-label={t("common.hide")}
        className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-bg-overlay hover:text-fg"
      >
        <X className="h-4 w-4" />
      </button>
    ) : null;

  if (state.kind === "free") {
    // На «Устройствах» об этом уже говорит подсказка про дубли — второй блок лишний.
    if (variant !== "home") return null;
    return (
      <div className="relative flex items-start gap-3 rounded-2xl border border-warning/30 bg-warning/5 p-4 sm:p-5">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-warning/15 text-warning">
          <Layers className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1 pr-6">
          <p className="text-sm font-semibold text-fg">{t("deviceUpsell.freeTitle")}</p>
          <p className="mt-0.5 text-sm text-fg-muted">
            {t("deviceUpsell.freeText", { n: state.slots })}
          </p>
          <Link
            to="/devices"
            className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-accent transition-opacity hover:opacity-80"
          >
            {t("deviceUpsell.freeCta")}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
        {closeButton}
      </div>
    );
  }

  // Докупка доступна и оплатима — показываем её ПЕРВОЙ. Тариф побольше остаётся
  // ниже строкой, если витрина уже пришла; ждать её ради этой строки не нужно.
  if (state.kind === "full" && extraPayable && extraOffer && extra && devices) {
    const upgradeLink =
      offers && upgrade && (variant !== "home" || homeAllows(changeTerms(offers, upgrade.plan, upgrade.days, upgrade.price.currency)))
        ? upgrade
        : null;
    return (
      <div
        className={
          variant === "home"
            ? "relative overflow-hidden rounded-2xl border border-accent/40 bg-gradient-to-br from-accent/15 to-accent-2/15 p-4 sm:p-5"
            : "rounded-xl border border-accent/30 bg-accent-subtle/40 p-3"
        }
      >
        <div className={variant === "home" ? "pr-6" : ""}>
          <ExtraDeviceHeadline offer={extraOffer} />
          <p className="mt-1 text-xs text-fg-muted">
            {t("deviceUpsell.full", { cur: devices.current_count, max: devices.max_count })}
          </p>
          <ExtraDeviceOffer
            data={extra}
            offer={extraOffer}
            onChanged={() => {
              loadExtra();
              onChanged?.();
            }}
          />
          {upgradeLink && (
            <p className="mt-3 text-xs text-fg-subtle">
              {t("extraDevice.orPlan")}{" "}
              <Link
                to={billingHref(upgradeLink.plan.public_code, upgradeLink.days)}
                className="font-medium text-accent transition-opacity hover:opacity-80"
              >
                {upgradeLink.plan.name}
              </Link>
            </p>
          )}
        </div>
        {closeButton}
      </div>
    );
  }

  if (state.kind !== "full" || !wantOffers || !offers || !upgrade || !devices) return null;

  const terms = changeTerms(offers, upgrade.plan, upgrade.days, upgrade.price.currency);
  if (variant === "home" && !homeAllows(terms)) return null;

  const { plan, days, price } = upgrade;
  const priceText = price.is_free
    ? t("billing.free")
    : `${price.final_amount} ${price.currency_symbol}`;

  return (
    <div
      className={
        variant === "home"
          ? "relative overflow-hidden rounded-2xl border border-accent/40 bg-gradient-to-br from-accent/15 to-accent-2/15 p-4 sm:p-5"
          : "rounded-xl border border-accent/30 bg-accent-subtle/40 p-3"
      }
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-accent">
          <MonitorSmartphone className="h-5 w-5" />
        </div>
        <div className={variant === "home" ? "min-w-0 flex-1 pr-6" : "min-w-0 flex-1"}>
          <p className="text-base font-bold text-fg">{t("deviceUpsell.title")}</p>
          <p className="mt-0.5 text-xs text-fg-muted sm:text-sm">
            {t("deviceUpsell.full", { cur: devices.current_count, max: devices.max_count })}
          </p>
          <p className="mt-2 text-sm text-fg">
            <span className="font-semibold">{plan.name}</span>
            {" · "}
            {plan.device_limit === 0
              ? t("home.devicesUnlimited")
              : t("billing.upToDevices", { n: plan.device_limit })}
          </p>
          <p className="tabular mt-0.5 text-xs text-fg-subtle">
            {priceText} · {t("billing.forDays", { days })}
          </p>
          {terms?.kind === "days" && (
            <p className="mt-2 rounded-lg border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-fg">
              {t("deviceUpsell.keepDays", { days: terms.days })}
            </p>
          )}
          {/* Остаток переносится: спокойная строка. Часть перенести нельзя — предупреждение
              вместо неё (две строки подряд «не пропадёт» и «пропадёт» спорили бы). */}
          {terms?.kind === "carry" && terms.lost === 0 && (
            <p className="mt-2 rounded-lg border border-border-subtle bg-bg-subtle px-2.5 py-1.5 text-xs text-fg-muted">
              {terms.samePlan
                ? t("billing.changeCarrySamePlan", { left: terms.left })
                : t("deviceUpsell.carryDays", { left: terms.left, bonus: terms.bonus })}
            </p>
          )}
          {terms?.kind === "carry" && terms.lost > 0 && (
            <p className="mt-2 rounded-lg border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-fg">
              {terms.lostReason === "cap"
                ? t("billing.changeCarryLostCap", { bonus: terms.bonus, lost: terms.lost })
                : t("billing.changeCarryLost", { bonus: terms.bonus, lost: terms.lost })}
            </p>
          )}
          <Link
            to={billingHref(plan.public_code, days)}
            className="btn-gradient mt-3 inline-flex items-center gap-1 rounded-xl px-3 py-2 text-sm font-semibold text-white"
          >
            {t("deviceUpsell.cta")}
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </div>
      {closeButton}
    </div>
  );
}
