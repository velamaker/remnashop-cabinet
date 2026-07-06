import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, X } from "lucide-react";
import type { SubscriptionInfoResponse } from "@/types/api";
import { daysUntil } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

// За сколько дней до конца показывать предупреждение.
const WARN_DAYS = 3;
const DISMISS_KEY = "renewal_banner_dismissed";

/**
 * Баннер «подписка заканчивается / истекла» с кнопкой «Продлить».
 * Дублирует напоминание из бота прямо в кабинете. Истёкшую подписку не прячем;
 * предупреждение (за несколько дней) можно скрыть на сессию.
 */
export function RenewalBanner({
  subscription,
}: {
  subscription: SubscriptionInfoResponse | null;
}) {
  const t = useT();
  const [dismissed, setDismissed] = useState(
    () => sessionStorage.getItem(DISMISS_KEY) === "1",
  );

  if (!subscription) return null;

  const days = daysUntil(subscription.expire_at);
  const expired =
    subscription.status === "EXPIRED" ||
    subscription.status === "DISABLED" ||
    days <= 0;
  const soon = !expired && days <= WARN_DAYS;

  if (!expired && !soon) return null;
  if (soon && dismissed) return null; // предупреждение можно скрыть, истёкшую — нет

  const dismiss = () => {
    sessionStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  };

  const title = expired
    ? t("renewal.expired")
    : days <= 1
      ? t("renewal.tomorrow")
      : t("renewal.inDays", { days });
  const text = expired
    ? t("renewal.expiredText")
    : t("renewal.soonText");

  return (
    <div
      className={`flex items-center gap-3 rounded-2xl border p-4 ${
        expired
          ? "border-danger/30 bg-danger/10"
          : "border-amber-400/30 bg-amber-400/10"
      }`}
    >
      <AlertTriangle
        className={`h-5 w-5 shrink-0 ${expired ? "text-danger" : "text-amber-500"}`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fg">{title}</p>
        <p className="text-xs text-fg-muted">{text}</p>
      </div>
      <Link
        to="/billing"
        className="btn-gradient inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-4 text-sm font-semibold transition-all active:scale-[0.98]"
      >
        {t("renewal.renew")} <ArrowRight className="h-4 w-4" />
      </Link>
      {soon && (
        <button
          type="button"
          onClick={dismiss}
          aria-label={t("common.hide")}
          className="shrink-0 text-fg-subtle transition-colors hover:text-fg"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
