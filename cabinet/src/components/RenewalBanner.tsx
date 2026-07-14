import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, X, Gauge } from "lucide-react";
import type { SubscriptionInfoResponse } from "@/types/api";
import { daysUntil, formatBytes, formatTrafficLimit } from "@/lib/format";
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
  const title = expired
    ? t("renewal.expired")
    : days <= 1
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

/** Предупреждение о трафике: прогресс-бар «использовано / лимит». */
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
            {formatBytes(used)} / {formatTrafficLimit(limit)}
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

  // Истечение
  const days = daysUntil(subscription.expire_at);
  const expired =
    subscription.status === "EXPIRED" ||
    subscription.status === "DISABLED" ||
    days <= 0;
  const soon = !expired && days <= WARN_DAYS;

  // Трафик (traffic_limit в байтах, 0 = безлимит)
  const limit = subscription.traffic_limit;
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
