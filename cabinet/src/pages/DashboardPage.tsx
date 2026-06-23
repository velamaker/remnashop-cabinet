import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Copy,
  RefreshCw,
  Check,
  Gift,
  ArrowRight,
  QrCode,
  X,
  Wallet,
  TrendingUp,
  ShoppingBag,
  ChevronRight,
  Smartphone,
  Users,
} from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useAuth } from "@/contexts/AuthContext";
import { useSubscription } from "@/hooks/useSubscription";
import { subscriptionApi } from "@/api/subscription";
import { referralApi } from "@/api/referral";
import { balanceApi, type BalanceResponse } from "@/api/balance";
import { ConnectGuide } from "@/components/ConnectGuide";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Input } from "@/components/ui/Input";
import {
  formatTrafficLimit,
  formatBytes,
  formatDate,
  daysUntil,
  formatRelativeOnline,
} from "@/lib/format";
import { ApiError } from "@/types/api";

function SubscriptionSkeleton() {
  return (
    <Card>
      <div className="flex items-center justify-between">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-6 w-20" />
      </div>
      <Skeleton className="mt-6 h-3 w-full" />
      <Skeleton className="mt-3 h-3 w-2/3" />
    </Card>
  );
}

function EmptySubscription() {
  const [isActivating, setIsActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleTrial = async () => {
    setIsActivating(true);
    setError(null);
    try {
      await subscriptionApi.activateTrial();
      window.location.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось активировать пробный период");
    } finally {
      setIsActivating(false);
    }
  };

  return (
    <div className="card-hero p-7 text-center sm:p-9">
      <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_10px_24px_-8px_var(--accent-glow)]">
        <Gift className="h-7 w-7" />
      </div>
      <h2 className="text-lg font-bold tracking-tight text-fg">У вас пока нет подписки</h2>
      <p className="mx-auto mt-1.5 max-w-sm text-sm text-fg-muted">
        Попробуйте бесплатный пробный период или сразу выберите подходящий тариф
      </p>
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
      <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
        <button
          onClick={handleTrial}
          disabled={isActivating}
          className="btn-gradient inline-flex h-10 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold transition-all active:scale-[0.98] disabled:opacity-60"
        >
          {isActivating ? "Активируем…" : "Активировать пробный период"}
        </button>
        <Link to="/billing">
          <Button variant="secondary" size="lg" className="rounded-xl">
            Выбрать тариф <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}

function ConnectionUrlCard({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const [showQr, setShowQr] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="surface p-5">
      <CardHeader title="Ссылка для подключения" subtitle="Импортируйте её в приложение или отсканируйте QR" />
      <div className="flex items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-subtle)] p-2">
        <code className="font-mono-tech flex-1 truncate px-2 text-xs text-fg-muted">{url}</code>
        <Button size="sm" variant="secondary" onClick={() => setShowQr((v) => !v)}>
          {showQr ? <X className="h-4 w-4" /> : <QrCode className="h-4 w-4" />}
        </Button>
        <Button size="sm" variant="secondary" onClick={handleCopy}>
          {copied ? (
            <Check className="h-4 w-4 text-success" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
          {copied ? "Скопировано" : "Копировать"}
        </Button>
      </div>
      {showQr && (
        <div className="mt-4 flex justify-center">
          <div className="rounded-2xl bg-white p-4 shadow-raised">
            <QRCodeSVG value={url} size={180} />
          </div>
        </div>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const { subscription, isLoading, reload } = useSubscription();
  const [isReissuing, setIsReissuing] = useState(false);
  const [reissueError, setReissueError] = useState<string | null>(null);
  const [balance, setBalance] = useState<BalanceResponse | null>(null);
  const [devices, setDevices] = useState<{ current: number; max: number } | null>(null);
  const [referrals, setReferrals] = useState<number | null>(null);

  useEffect(() => {
    balanceApi.get().then(setBalance).catch(() => {});
    subscriptionApi.devices().then((d) => setDevices({ current: d.current_count, max: d.max_count })).catch(() => {});
    referralApi.program().then((p) => setReferrals(p.invited_count)).catch(() => {});
  }, []);

  const handleReissue = async () => {
    setIsReissuing(true);
    setReissueError(null);
    try {
      await subscriptionApi.reissue();
      await reload();
    } catch (e) {
      setReissueError(
        e instanceof ApiError ? e.detail : "Не удалось перевыпустить подписку",
      );
    } finally {
      setIsReissuing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-5">
        <Skeleton className="h-8 w-64" />
        <SubscriptionSkeleton />
      </div>
    );
  }

  if (!subscription) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-2xl font-bold tracking-tight text-fg">
          Добро пожаловать{user?.name ? `, ${user.name.trim().split(/\s+/)[0]}` : ""}!
        </h1>
        <EmptySubscription />
      </div>
    );
  }

  const remainingDays = daysUntil(subscription.expire_at);
  const isUnlimited = subscription.traffic_limit === 0;

  return (
    <div className="flex flex-col gap-5">
      {/* Заголовок */}
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-fg">Подписка</h1>
        <StatusBadge status={subscription.status} />
      </div>

      <div className="card-hero p-6 sm:p-7">
        {/* Тариф */}
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-fg-subtle">
          Текущий тариф
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="text-2xl font-bold tracking-tight text-fg">{subscription.plan_name}</h2>
          {subscription.is_trial && (
            <span className="rounded-full border border-accent/30 bg-accent-subtle px-2.5 py-0.5 text-xs font-medium text-accent">
              Пробный период
            </span>
          )}
        </div>

        {/* Технические данные с хайрлайн-разделителями */}
        <div className="mt-6 grid grid-cols-2 gap-y-5 sm:grid-cols-3 sm:gap-0">
          <div className="sm:pr-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              Действует до
            </p>
            <p className="tabular mt-1.5 text-[15px] font-semibold text-fg">
              {formatDate(subscription.expire_at)}
            </p>
            <p className="tabular mt-0.5 text-xs text-fg-muted">
              {remainingDays > 0 ? `${remainingDays} дн. осталось` : "истекла"}
            </p>
          </div>
          <div className="sm:border-l sm:border-[var(--border)] sm:pl-4 sm:pr-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              Устройства
            </p>
            <p className="tabular mt-1.5 text-[15px] font-semibold text-fg">
              до {subscription.device_limit}
            </p>
            <p className="mt-0.5 text-xs text-fg-muted">одновременных</p>
          </div>
          <div className="sm:border-l sm:border-[var(--border)] sm:pl-4">
            <p className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              Активность
            </p>
            <p className="mt-1.5 text-[15px] font-semibold text-fg">
              {formatRelativeOnline(subscription.online_at)}
            </p>
            <p className="mt-0.5 text-xs text-fg-muted">последний сеанс</p>
          </div>
        </div>

        {/* Трафик */}
        <div className="mt-6 border-t border-[var(--border-subtle)] pt-5">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              Трафик
            </span>
            {isUnlimited ? (
              <span className="tabular inline-flex items-center gap-1.5 text-sm font-medium text-fg">
                {formatBytes(subscription.used_traffic_bytes)}
                <span className="text-fg-subtle">·</span>
                <span className="text-accent">безлимит</span>
              </span>
            ) : (
              <span className="tabular text-sm font-medium text-fg">
                {formatBytes(subscription.used_traffic_bytes)}{" "}
                <span className="text-fg-subtle">
                  из {formatTrafficLimit(subscription.traffic_limit)}
                </span>
              </span>
            )}
          </div>
          {isUnlimited ? (
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--border-subtle)]">
              <div className="h-full w-full rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] opacity-60" />
            </div>
          ) : (
            <ProgressBar
              value={subscription.used_traffic_bytes || 0}
              max={subscription.traffic_limit}
            />
          )}
        </div>

        {reissueError && <p className="mt-4 text-sm text-danger">{reissueError}</p>}

        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            to="/billing"
            className="btn-gradient inline-flex h-10 items-center justify-center gap-2 rounded-xl px-5 text-sm font-semibold transition-all active:scale-[0.98]"
          >
            Продлить / сменить тариф
          </Link>
          <Button
            variant="secondary"
            size="lg"
            className="rounded-xl"
            onClick={handleReissue}
            isLoading={isReissuing}
          >
            <RefreshCw className="h-4 w-4" />
            Перевыпустить ссылку
          </Button>
        </div>
      </div>

      {/* Баланс */}
      <div className="surface p-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
            <Wallet className="h-4 w-4 text-accent" />
            Баланс
          </h3>
          <Link
            to="/balance"
            className="inline-flex items-center gap-1 text-xs font-medium text-accent transition-opacity hover:opacity-80"
          >
            История <ChevronRight className="h-3.5 w-3.5" />
          </Link>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-3">
            <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              <Wallet className="h-3.5 w-3.5" /> Баллы
            </p>
            <p className="tabular mt-1 text-lg font-bold text-accent">{balance?.points ?? 0}</p>
          </div>
          <div className="rounded-xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-3">
            <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              <TrendingUp className="h-3.5 w-3.5" /> Потрачено
            </p>
            <p className="tabular mt-1 text-lg font-bold text-fg">
              {(balance?.total_spent ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 0 })}{" "}
              <span className="text-xs font-medium text-fg-subtle">₽</span>
            </p>
          </div>
          <div className="rounded-xl border border-[var(--border-subtle)] bg-bg-subtle/60 p-3">
            <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-fg-subtle">
              <ShoppingBag className="h-3.5 w-3.5" /> Покупок
            </p>
            <p className="tabular mt-1 text-lg font-bold text-fg">{balance?.total_purchases ?? 0}</p>
          </div>
        </div>
      </div>

      {/* Устройства + Рефералы */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Link to="/devices" className="surface group flex items-center gap-3 p-4 transition-all hover:-translate-y-0.5 hover:border-[var(--accent)]">
          <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-accent-subtle text-accent">
            <Smartphone className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-fg">Устройства</p>
            <p className="tabular text-xs text-fg-muted">
              {devices ? `${devices.current} из ${devices.max} · управление` : `до ${subscription.device_limit} · управление`}
            </p>
          </div>
          <ChevronRight className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-colors group-hover:text-accent" />
        </Link>

        <Link to="/referral" className="surface group flex items-center gap-3 p-4 transition-all hover:-translate-y-0.5 hover:border-[var(--accent)]">
          <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-accent-subtle text-accent">
            <Users className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-fg">Рефералы</p>
            <p className="tabular text-xs text-fg-muted">
              {referrals ?? 0} приглашено · пригласить
            </p>
          </div>
          <ChevronRight className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-colors group-hover:text-accent" />
        </Link>
      </div>

      {/* Подключить устройство — прямо здесь */}
      <ConnectGuide subUrl={subscription.url} />

      <ConnectionUrlCard url={subscription.url} />

      <PromocodeCard onActivated={reload} />
    </div>
  );
}

function PromocodeCard({ onActivated }: { onActivated: () => void }) {
  const [code, setCode] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    setIsLoading(true);
    setMessage(null);
    try {
      await subscriptionApi.activatePromocode(code.trim());
      setMessage({ type: "success", text: "Промокод активирован!" });
      setCode("");
      onActivated();
    } catch (e) {
      setMessage({
        type: "error",
        text: e instanceof ApiError ? e.detail : "Не удалось активировать промокод",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="surface p-5">
      <CardHeader title="Промокод" subtitle="Есть код от друга или из рекламной акции?" />
      <form onSubmit={handleSubmit} className="flex gap-2">
        <Input
          name="promo"
          placeholder="PROMO2026"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          className="flex-1"
        />
        <Button type="submit" variant="secondary" isLoading={isLoading}>
          Применить
        </Button>
      </form>
      {message && (
        <p
          className={`mt-2 text-sm ${
            message.type === "success" ? "text-success" : "text-danger"
          }`}
        >
          {message.text}
        </p>
      )}
    </div>
  );
}
