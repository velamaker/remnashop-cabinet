import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Infinity as InfinityIcon,
  MonitorSmartphone,
  CalendarClock,
  Server,
  Users,
  ArrowRight,
  RefreshCw,
  Gift,
  ChevronRight,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useT } from "@/i18n/I18nContext";
import { useSubscription } from "@/hooks/useSubscription";
import { subscriptionApi } from "@/api/subscription";
import { referralApi } from "@/api/referral";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { TelegramLinkPrompt } from "@/components/TelegramLinkPrompt";
import { RenewalBanner } from "@/components/RenewalBanner";
import { TrialDiscountBanner } from "@/components/TrialDiscountBanner";
import { PromoBanner } from "@/components/PromoBanner";
import { OnboardingWizard } from "@/components/OnboardingWizard";
import { TrafficChart } from "@/components/TrafficChart";
import { ServerStatusCard } from "@/components/ServerStatusCard";
import { formatBytes, formatTrafficLimit, formatDate, daysUntil } from "@/lib/format";
import { Flag } from "@/components/Flag";
import { ApiError } from "@/types/api";

const REFRESH_SECONDS = 60 * 60; // автообновление раз в час

/** Флаг страны — локальный self-hosted пакет (см. components/Flag). */
function CountryFlag({ code }: { code: string }) {
  return <Flag code={code} className="h-5 w-7" />;
}

export default function HomePage() {
  const { user } = useAuth();
  const t = useT();
  const { subscription, isLoading, reload } = useSubscription();
  const [devices, setDevices] = useState<{ current: number; max: number } | null>(null);
  const [referrals, setReferrals] = useState<number | null>(null);
  const [favServer, setFavServer] = useState<{ name: string; country_code: string; total: number } | null>(null);
  const [servers, setServers] = useState<{ name: string; country_code: string; total: number }[]>([]);
  const [trialError, setTrialError] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_SECONDS);
  const [trial, setTrial] = useState<{ available: boolean; days: number; traffic_gb: number; devices: number } | null>(null);

  const loadExtras = useCallback(() => {
    subscriptionApi.devices().then((d) => setDevices({ current: d.current_count, max: d.max_count })).catch(() => {});
    referralApi.program().then((p) => setReferrals(p.invited_count)).catch(() => {});
    subscriptionApi.serverStats().then((s) => { setFavServer(s.favorite); setServers(s.nodes); }).catch(() => {});
  }, []);

  // Инфо о пробном периоде — только когда нет подписки (для карточки-предложения).
  useEffect(() => {
    if (!isLoading && !subscription) {
      subscriptionApi.trialInfo().then(setTrial).catch(() => setTrial(null));
    }
  }, [isLoading, subscription]);

  useEffect(() => {
    loadExtras();
  }, [loadExtras]);

  // Автообновление раз в час
  const reloadRef = useRef(() => {});
  reloadRef.current = () => {
    reload();
    loadExtras();
  };
  useEffect(() => {
    const id = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          reloadRef.current();
          return REFRESH_SECONDS;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const firstName = user?.name?.trim().split(/\s+/)[0] || "";

  const handleTrial = async () => {
    setActivating(true);
    setTrialError(null);
    try {
      await subscriptionApi.activateTrial();
      window.location.reload();
    } catch (e) {
      setTrialError(e instanceof ApiError ? e.detail : t("sub.trialErr"));
    } finally {
      setActivating(false);
    }
  };

  const isUnlimited = subscription?.traffic_limit === 0;
  const remainingDays = subscription ? daysUntil(subscription.expire_at) : 0;
  const used = subscription?.used_traffic_bytes || 0;
  const usedPct = subscription && !isUnlimited && subscription.traffic_limit > 0
    ? Math.min(100, (used / subscription.traffic_limit) * 100)
    : 100;

  return (
    <div className="flex flex-col gap-5">
      {/* Приветствие */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-fg sm:text-[28px]">
          {t("home.welcome")}{firstName ? `, ${firstName}` : ""}!
        </h1>
        <div className="mt-1.5 flex items-center gap-2">
          <span className="text-sm text-fg-muted">{t("home.yourSubscription")}</span>
          {subscription && (
            <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent-subtle px-2.5 py-0.5 text-xs font-medium text-accent">
              ★ {subscription.is_trial ? t("home.trial") : t("home.active")}
            </span>
          )}
        </div>
      </div>

      {/* Напоминание продлить подписку (скоро кончится / истекла) */}
      <PromoBanner />
      {subscription?.url && <OnboardingWizard subUrl={subscription.url} />}
      <RenewalBanner subscription={subscription} />
      <TrialDiscountBanner />

      {/* Ненавязчивое предложение привязать Telegram (только email-пользователям) */}
      <TelegramLinkPrompt />

      {/* Явная кнопка «Подключиться» — чтобы не искали, где подключаться.
          Видна на мобильной и ПК-версии, когда подписка активна. */}
      {subscription && (
        <Link
          to="/devices"
          className="btn-gradient flex h-14 items-center justify-center gap-2.5 rounded-2xl px-6 text-base font-semibold transition-all active:scale-[0.99]"
        >
          <MonitorSmartphone className="h-5 w-5" />
          {t("home.connect")}
          <ArrowRight className="h-5 w-5" />
        </Link>
      )}

      {isLoading ? (
        <Skeleton className="h-72 w-full rounded-[22px]" />
      ) : !subscription ? (
        <div className="card-hero p-7 text-center sm:p-9">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_10px_24px_-8px_var(--accent-glow)]">
            <Gift className="h-7 w-7" />
          </div>
          <h2 className="text-lg font-bold tracking-tight text-fg">
            {trial?.available ? t("home.startFree") : t("home.noSub.title")}
          </h2>
          <p className="mx-auto mt-1.5 max-w-sm text-sm text-fg-muted">
            {trial?.available
              ? t("home.trialOffer", { days: trial.days })
              : t("home.noSub.subtitle")}
          </p>

          {trial?.available && (
            <div className="mt-4 flex flex-wrap justify-center gap-2 text-xs font-medium text-fg-muted">
              <span className="rounded-full border border-[var(--border)] bg-bg-raised px-3 py-1">🗓 {t("home.trialDays", { n: trial.days })}</span>
              <span className="rounded-full border border-[var(--border)] bg-bg-raised px-3 py-1">
                📊 {trial.traffic_gb === 0 ? t("home.trialUnlimited") : t("home.trafficGb", { n: trial.traffic_gb })}
              </span>
              <span className="rounded-full border border-[var(--border)] bg-bg-raised px-3 py-1">
                📱 {trial.devices === 0 ? t("home.devicesUnlimited") : t("billing.upToDevices", { n: trial.devices })}
              </span>
            </div>
          )}

          {trialError && <p className="mt-3 text-sm text-danger">{trialError}</p>}

          <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            {trial?.available && (
              <button
                onClick={handleTrial}
                disabled={activating}
                className="btn-gradient inline-flex h-11 items-center justify-center gap-2 rounded-xl px-6 text-sm font-semibold transition-all active:scale-[0.98] disabled:opacity-60"
              >
                {activating ? t("home.activating") : t("home.activateTrialDays", { days: trial.days })}
              </button>
            )}
            <Link to="/billing">
              <Button variant={trial?.available ? "secondary" : "primary"} size="lg" className="rounded-xl">
                {t("home.choosePlan")} <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </div>
        </div>
      ) : (
        /* Hero: расход трафика */
        <div className="card-hero p-6 sm:p-7">
          <div className="flex items-start justify-between">
            <div>
              <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                {isUnlimited ? t("home.unlimited") : t("home.active")}
              </span>
              <h2 className="mt-1.5 text-xl font-bold tracking-tight text-fg">{t("home.trafficUsage")}</h2>
            </div>
            <div className="flex items-center gap-3 text-right">
              <span className="tabular text-xs text-fg-subtle">
                {isUnlimited ? t("home.trafficUnlimited", { used: formatBytes(used) }) : t("home.trafficOf", { used: formatBytes(used), limit: formatTrafficLimit(subscription.traffic_limit) })}
              </span>
              {isUnlimited && <InfinityIcon className="h-5 w-5 text-accent" />}
            </div>
          </div>

          {/* Прогресс */}
          <div className="mt-4 h-2.5 w-full overflow-hidden rounded-full bg-[var(--border-subtle)]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] transition-all duration-500"
              style={{ width: `${usedPct}%`, opacity: isUnlimited ? 0.7 : 1 }}
            />
          </div>

          {/* Подключить устройство */}
          <Link
            to="/devices"
            className="group mt-5 flex items-center gap-3 rounded-2xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-4 transition-colors hover:border-[var(--accent)]"
          >
            <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-accent-subtle text-accent">
              <MonitorSmartphone className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-fg">{t("home.connectDevice")}</p>
              <p className="tabular text-xs text-fg-muted">
                {devices ? t("home.devicesConnected", { cur: devices.current, max: devices.max }) : t("home.devicesUpTo", { limit: subscription.device_limit })}
              </p>
            </div>
            {devices && (
              <div className="hidden items-center gap-1 sm:flex">
                {Array.from({ length: Math.min(devices.max, 10) }).map((_, i) => (
                  <span
                    key={i}
                    className={`h-1.5 w-1.5 rounded-full ${i < devices.current ? "bg-accent" : "bg-[var(--border)]"}`}
                  />
                ))}
              </div>
            )}
            <ChevronRight className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-colors group-hover:text-accent" />
          </Link>

          {/* Тариф / Осталось */}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-4">
              <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
                {t("home.plan")}
              </p>
              <p className="mt-1.5 truncate text-[15px] font-semibold text-fg">{subscription.plan_name}</p>
              <p className="tabular mt-0.5 text-xs text-fg-subtle">{t("home.until", { date: formatDate(subscription.expire_at) })}</p>
            </div>
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-4">
              <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
                <CalendarClock className="h-3.5 w-3.5" />
                {t("home.remaining")}
              </p>
              <p className="tabular mt-1.5 text-2xl font-bold text-fg">
                {remainingDays > 0 ? remainingDays : 0}
                <span className="ml-1 text-sm font-medium text-fg-subtle">{t("home.daysShort")}</span>
              </p>
            </div>
          </div>

          {/* Футер */}
          <div className="mt-5 flex items-center justify-between border-t border-[var(--border-subtle)] pt-4">
            <span className="tabular inline-flex items-center gap-1.5 text-xs text-fg-subtle">
              <RefreshCw className="h-3.5 w-3.5" />
              {t("home.minutes", { n: Math.ceil(countdown / 60) })}
            </span>
            <Link
              to="/subscription"
              className="inline-flex items-center gap-1 text-sm font-medium text-accent transition-opacity hover:opacity-80"
            >
              {t("home.manageSubscription")}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      )}

      {/* Любимый сервер + Рефералы */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="surface flex items-start justify-between gap-3 p-5">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-medium text-fg-muted">
              <Server className="h-4 w-4" />
              {t("home.favoriteServer")}
            </p>
            {favServer ? (
              <>
                <p className="mt-2 flex items-center gap-2 text-xl font-bold text-fg">
                  <CountryFlag code={favServer.country_code} />
                  <span className="truncate">{favServer.name}</span>
                </p>
                <p className="tabular mt-0.5 text-xs text-fg-subtle">
                  {t("home.per30days", { value: formatBytes(favServer.total) })}
                </p>
              </>
            ) : (
              <>
                <p className="mt-2 text-xl font-bold text-fg-subtle">—</p>
                <p className="mt-0.5 text-xs text-fg-subtle">{t("home.noData")}</p>
              </>
            )}
          </div>
        </div>

        <Link to="/referral" className="surface group flex items-start justify-between gap-3 p-5 transition-all hover:-translate-y-0.5 hover:border-[var(--accent)]">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-fg-muted">
              <Users className="h-4 w-4" />
              {t("home.referrals")}
            </p>
            <p className="tabular mt-2 text-2xl font-bold text-fg">{referrals ?? 0}</p>
            <p className="mt-0.5 text-xs text-fg-subtle">{t("home.invited")}</p>
          </div>
          <ChevronRight className="h-4 w-4 text-fg-subtle transition-colors group-hover:text-accent" />
        </Link>
      </div>

      {/* Наши серверы — список нод с относительной загрузкой по трафику за 30 дней */}
      {servers.length > 0 && (
        <div className="surface p-5">
          <div className="mb-3 flex items-center justify-between">
            <p className="flex items-center gap-2 text-sm font-medium text-fg-muted">
              <Server className="h-4 w-4" />
              {t("home.ourServers")}
            </p>
            <span className="text-xs text-fg-subtle">{t("home.activeCount", { n: servers.length })}</span>
          </div>
          <div className="flex flex-col gap-3">
            {servers.map((n, i) => {
              const max = Math.max(...servers.map((s) => s.total), 1);
              const pct = Math.round((n.total / max) * 100);
              return (
                <div key={i} className="flex items-center gap-3">
                  <CountryFlag code={n.country_code} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-fg">{n.name}</span>
                      <span className="tabular shrink-0 text-xs text-fg-subtle">{formatBytes(n.total)}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-raised">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-xs text-fg-subtle">{t("home.loadBy30days")}</p>
        </div>
      )}

      {/* Статус серверов — онлайн/офлайн + пинг с устройства пользователя */}
      <ServerStatusCard />

      {/* График расхода трафика по дням */}
      <TrafficChart />
    </div>
  );
}
