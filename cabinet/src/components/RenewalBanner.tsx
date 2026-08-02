import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, X, Gauge } from "lucide-react";
import type { SubscriptionInfoResponse } from "@/types/api";
import { daysUntil, isExpired, formatBytes, formatTrafficLimit, trafficLimitBytes } from "@/lib/format";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useT } from "@/i18n/I18nContext";

// За сколько дней до конца показывать предупреждение об истечении.
const WARN_DAYS = 3;
// С какого % израсходованного трафика показывать предупреждение.
const TRAFFIC_WARN_PCT = 80;
const DISMISS_EXPIRY = "renewal_banner_dismissed";
const DISMISS_TRAFFIC = "traffic_banner_dismissed";

/** Крупная плашка «Продлить» — истёкшую/заканчивающуюся подписку не спрячешь. */
function ExpiryBanner({
  expired,
  days,
  soon,
  onDismiss,
}: {
  expired: boolean;
  days: number;
  soon: boolean;
  onDismiss: () => void;
}) {
  const t = useT();
  // days = 0 — это «меньше суток», то есть заканчивается уже завтра (раньше при
  // округлении вверх тот же случай давал 1).
  const title = expired
    ? t("renewal.expired")
    : days <= 0
      ? t("renewal.tomorrow")
      : t("renewal.inDays", { days });
  const text = expired ? t("renewal.expiredText") : t("renewal.soonText");

  return (
    <div
      className={`rounded-2xl border p-4 sm:p-5 ${
        expired ? "border-danger/40 bg-danger/10" : "border-amber-400/40 bg-amber-400/10"
      }`}
    >
      <div className="flex items-center gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
            expired ? "bg-danger/15 text-danger" : "bg-amber-400/15 text-amber-500"
          }`}
        >
          <AlertTriangle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className={`text-base font-bold ${expired ? "text-danger" : "text-fg"}`}>{title}</p>
          <p className="mt-0.5 text-xs text-fg-muted sm:text-sm">{text}</p>
        </div>
        {soon && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label={t("common.hide")}
            className="shrink-0 text-fg-subtle transition-colors hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
      <Link
        to="/billing"
        className="btn-gradient mt-3 flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition-all active:scale-[0.98]"
      >
        {t("renewal.renew")} <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}

/** Предупреждение о трафике: прогресс-бар «использовано / лимит» (оба в байтах). */
function TrafficBanner({
  out,
  used,
  limit,
  onDismiss,
}: {
  out: boolean;
  used: number;
  limit: number;
  onDismiss: () => void;
}) {
  const t = useT();
  return (
    <div
      className={`rounded-2xl border p-4 sm:p-5 ${
        out ? "border-danger/40 bg-danger/10" : "border-amber-400/40 bg-amber-400/10"
      }`}
    >
      <div className="flex items-center gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
            out ? "bg-danger/15 text-danger" : "bg-amber-400/15 text-amber-500"
          }`}
        >
          <Gauge className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className={`text-base font-bold ${out ? "text-danger" : "text-fg"}`}>
            {out ? t("renewal.trafficOut") : t("renewal.trafficWarn")}
          </p>
          <p className="mt-0.5 text-xs text-fg-muted sm:text-sm tabular">
            {formatBytes(used)} / {formatBytes(limit)}
          </p>
        </div>
        {!out && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label={t("common.hide")}
            className="shrink-0 text-fg-subtle transition-colors hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
      <ProgressBar value={used} max={limit} className="mt-3" />
      {out && (
        <Link
          to="/billing"
          className="btn-gradient mt-3 flex h-11 w-full items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition-all active:scale-[0.98]"
        >
          {t("renewal.renew")} <ArrowRight className="h-4 w-4" />
        </Link>
      )}
    </div>
  );
}

/**
 * Заметные предупреждения по подписке: (1) истечение периода → «Продлить»,
 * (2) окончание трафика с прогресс-баром. Истёкшую подписку / исчерпанный
 * трафик не прячем; «мягкие» предупреждения (за N дней / ≥80%) можно скрыть
 * на сессию. Без блока «резервный сервер» (по требованию владельца).
 */
export function RenewalBanner({
  subscription,
}: {
  subscription: SubscriptionInfoResponse | null;
}) {
  const [expiryDismissed, setExpiryDismissed] = useState(
    () => sessionStorage.getItem(DISMISS_EXPIRY) === "1",
  );
  const [trafficDismissed, setTrafficDismissed] = useState(
    () => sessionStorage.getItem(DISMISS_TRAFFIC) === "1",
  );

  if (!subscription) return null;

  // Истечение. Пауза — не истечение: подписка на паузе тоже DISABLED, но звать
  // человека «продлить» здесь нельзя — он сам её остановил и вернёт кнопкой.
  // days — ПОЛНЫЕ оставшиеся сутки (см. daysUntil): 0 = «меньше суток», а не
  // «истекла». Поэтому истечение проверяем по самой дате, иначе последний день
  // подписки объявлялся бы просроченным.
  // Поля frozen может не быть вовсе (бэкенды без паузы) — тогда всё как раньше.
  const paused = subscription.frozen === true;
  const days = daysUntil(subscription.expire_at);
  const expired =
    !paused &&
    (subscription.status === "EXPIRED" ||
      subscription.status === "DISABLED" ||
      isExpired(subscription.expire_at));
  // `< WARN_DAYS` при округлении вниз — ровно тот же порог, что раньше давал
  // `<= WARN_DAYS` при округлении вверх: баннер по-прежнему за 3 суток до конца.
  // На паузе срок не идёт (при снятии дни возвращаются), поэтому «заканчивается
  // через N дней» здесь тоже неправда — мягкое предупреждение молчит.
  const soon = !paused && !expired && days < WARN_DAYS;

  // Трафик: лимит из API в ГБ (0 = безлимит), расход в байтах — переводим лимит,
  // иначе «трафик исчерпан» показывается всем, у кого лимит вообще задан.
  const limit = trafficLimitBytes(subscription.traffic_limit);
  const used = subscription.used_traffic_bytes || 0;
  const isUnlimited = limit === 0;
  const pct = !isUnlimited && limit > 0 ? (used / limit) * 100 : 0;
  const trafficOut = !isUnlimited && limit > 0 && used >= limit;
  const trafficLow = !isUnlimited && !trafficOut && pct >= TRAFFIC_WARN_PCT;

  const showExpiry = expired || (soon && !expiryDismissed);
  const showTraffic = trafficOut || (trafficLow && !trafficDismissed);
  if (!showExpiry && !showTraffic) return null;

  const dismissExpiry = () => {
    sessionStorage.setItem(DISMISS_EXPIRY, "1");
    setExpiryDismissed(true);
  };
  const dismissTraffic = () => {
    sessionStorage.setItem(DISMISS_TRAFFIC, "1");
    setTrafficDismissed(true);
  };

  return (
    <div className="space-y-3">
      {showExpiry && (
        <ExpiryBanner expired={expired} days={days} soon={soon} onDismiss={dismissExpiry} />
      )}
      {showTraffic && (
        <TrafficBanner out={trafficOut} used={used} limit={limit} onDismiss={dismissTraffic} />
      )}
    </div>
  );
}
