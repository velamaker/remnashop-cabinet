import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, Check, Users, Gift, Mail, CreditCard, ArrowRight, QrCode, X, Coins } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { referralApi } from "@/api/referral";
import type { ReferralEarningsResponse } from "@/api/referral";
import { useAuth } from "@/contexts/AuthContext";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import type { ReferralProgramResponse } from "@/types/api";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

// Единица измерения уровня зависит от СТРАТЕГИИ (а не типа награды): при PERCENT
// значение уровня — это процент от платежа друга; иначе — фикс. величина в единицах
// типа награды (баллы/дни). Раньше бралось по reward_type (POINTS) → показывалось
// сырое «15 POINTS»; теперь «15 % от платежей» / «15 баллов» / «15 дней подписки».
function rewardUnitKey(strategy: string, rewardType: string): string {
  if (strategy === "PERCENT") return "ref.unitPercent";
  if (rewardType === "EXTRA_DAYS") return "ref.unitDays";
  if (rewardType === "POINTS") return "ref.unitPoints";
  return "ref.unitFixed";
}

function ReferralUnavailable({
  reason,
}: {
  reason: "email" | "subscription" | "disabled";
}) {
  const t = useT();
  const content = {
    email: {
      icon: Mail,
      title: t("ref.emailTitle"),
      text: t("ref.emailText"),
      action: (
        <Link to="/settings">
          <Button size="sm">
            {t("ref.emailAction")} <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      ),
    },
    subscription: {
      icon: CreditCard,
      title: t("ref.subTitle"),
      text: t("ref.subText"),
      action: (
        <Link to="/billing">
          <Button size="sm">
            {t("ref.subAction")} <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      ),
    },
    disabled: {
      icon: Gift,
      title: t("ref.disabledTitle"),
      text: t("ref.disabledText"),
      action: null,
    },
  }[reason];

  const Icon = content.icon;

  return (
    <Card className="bg-grain text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-subtle">
        <Icon className="h-6 w-6 text-accent" />
      </div>
      <h2 className="text-base font-semibold text-fg">{content.title}</h2>
      <p className="mx-auto mt-1 max-w-xs text-sm text-fg-subtle">{content.text}</p>
      {content.action && <div className="mt-5">{content.action}</div>}
    </Card>
  );
}

function ReferralCodeCard({ code }: { code: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const referralLink = `${window.location.origin}/register?ref=${code}`;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(referralLink);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Card className="bg-grain">
      <CardHeader title={t("ref.linkTitle")} subtitle={t("ref.linkSubtitle")} />
      <div className="flex items-center gap-2 rounded-xl bg-bg-subtle p-2">
        <code className="flex-1 truncate px-2 text-xs text-fg-muted">{referralLink}</code>
        <Button size="sm" variant="secondary" onClick={handleCopy}>
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          {copied ? t("connect.copied") : t("connect.copy")}
        </Button>
      </div>

      {/* QR реферальной ссылки — показать другу для сканирования */}
      <button
        type="button"
        onClick={() => setShowQr((v) => !v)}
        className="mt-3 inline-flex items-center gap-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
      >
        {showQr ? <X className="h-4 w-4" /> : <QrCode className="h-4 w-4" />}
        {showQr ? t("ref.hideQr") : t("ref.showQr")}
      </button>
      {showQr && (
        <div className="mt-3 flex flex-col items-center gap-2">
          <div className="rounded-2xl bg-white p-3">
            <QRCodeSVG value={referralLink} size={180} />
          </div>
          <p className="max-w-xs text-center text-xs text-fg-subtle">
            {t("ref.qrHint")}
          </p>
        </div>
      )}
    </Card>
  );
}

function StatsRow({ program }: { program: ReferralProgramResponse }) {
  const t = useT();
  return (
    <div className="grid grid-cols-2 gap-3">
      <Card variant="bordered" className="text-center">
        <Users className="mx-auto mb-2 h-5 w-5 text-fg-subtle" />
        <p className="text-2xl font-semibold text-fg">{program.invited_count}</p>
        <p className="text-xs text-fg-subtle">{t("ref.invited")}</p>
      </Card>
      <Card variant="bordered" className="text-center">
        <CreditCard className="mx-auto mb-2 h-5 w-5 text-fg-subtle" />
        <p className="text-2xl font-semibold text-fg">
          {program.invited_with_payment_count}
        </p>
        <p className="text-xs text-fg-subtle">{t("ref.paid")}</p>
      </Card>
    </div>
  );
}

function EarningsCard({
  earnings,
  rewardType,
}: {
  earnings: ReferralEarningsResponse;
  rewardType: string;
}) {
  const t = useT();
  // referral_rewards.amount: для EXTRA_DAYS — дни, иначе (POINTS/деньги) — рубли.
  const isDays = rewardType === "EXTRA_DAYS";
  const value = isDays
    ? t("ref.earnedDays", { n: earnings.earned })
    : `${earnings.earned.toLocaleString()} ₽`;

  return (
    <div className="card-accent rounded-xl p-5">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent-subtle">
          <Coins className="h-5 w-5 text-accent" />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-fg-subtle">{t("ref.earnedTitle")}</p>
          <p className="text-2xl font-semibold text-fg">{value}</p>
          {earnings.rewards_count > 0 && (
            <p className="mt-0.5 text-xs text-fg-subtle">
              {t("ref.earnedCount", { n: earnings.rewards_count })}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function RewardLevelsCard({ program }: { program: ReferralProgramResponse }) {
  const t = useT();
  const unit = t(rewardUnitKey(program.reward_strategy, program.reward_type));

  return (
    <Card variant="bordered">
      <CardHeader title={t("ref.levelsTitle")} />
      <div className="flex flex-col gap-2">
        {program.reward_levels.map((level) => (
          <div
            key={level.level}
            className="flex items-center justify-between rounded-xl bg-bg-subtle px-3 py-2.5"
          >
            <span className="text-sm text-fg-muted">{t("ref.level", { n: level.level })}</span>
            <span className="text-sm font-medium text-fg">
              {level.value} {unit}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function ReferralPage() {
  const t = useT();
  const { user } = useAuth();
  const [program, setProgram] = useState<ReferralProgramResponse | null>(null);
  const [earnings, setEarnings] = useState<ReferralEarningsResponse | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<
    "email" | "subscription" | "disabled" | null
  >(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setUnavailableReason(null);
    try {
      const data = await referralApi.program();
      setProgram(data);
      // Заработок — некритично: если ручка недоступна, просто не показываем карточку.
      try {
        setEarnings(await referralApi.earnings());
      } catch {
        setEarnings(null);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        if (!user?.is_email_verified && user?.auth_type?.toUpperCase() !== "TELEGRAM") {
          setUnavailableReason("email");
        } else if (e.detail.toLowerCase().includes("subscription")) {
          setUnavailableReason("subscription");
        } else {
          setUnavailableReason("disabled");
        }
      } else {
        setError(e instanceof ApiError ? e.detail : t("ref.errLoad"));
      }
    } finally {
      setIsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    load();
  }, [load]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-5">
        <h1 className="text-xl font-semibold text-fg">{t("ref.title")}</h1>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-fg">{t("ref.title")}</h1>

      {error && <p className="text-sm text-danger">{error}</p>}
      {unavailableReason && <ReferralUnavailable reason={unavailableReason} />}

      {program && (
        <>
          <ReferralCodeCard code={program.referral_code} />
          <StatsRow program={program} />
          {earnings && earnings.earned > 0 && (
            <EarningsCard earnings={earnings} rewardType={program.reward_type} />
          )}
          {program.reward_levels.length > 0 && <RewardLevelsCard program={program} />}
        </>
      )}
    </div>
  );
}
