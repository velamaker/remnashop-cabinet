import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy, Check, Users, Gift, Mail, CreditCard, ArrowRight } from "lucide-react";
import { referralApi } from "@/api/referral";
import { useAuth } from "@/contexts/AuthContext";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import type { ReferralProgramResponse } from "@/types/api";
import { ApiError } from "@/types/api";

const rewardTypeLabels: Record<string, string> = {
  PERCENT: "% от платежей",
  FIXED: "фиксированная сумма",
  DAYS: "дней подписки",
};

function ReferralUnavailable({
  reason,
}: {
  reason: "email" | "subscription" | "disabled";
}) {
  const content = {
    email: {
      icon: Mail,
      title: "Подтвердите email",
      text: "Реферальная программа доступна только пользователям с подтверждённой почтой.",
      action: (
        <Link to="/settings">
          <Button size="sm">
            Подтвердить email <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      ),
    },
    subscription: {
      icon: CreditCard,
      title: "Нужна активная подписка",
      text: "Чтобы участвовать в программе и приглашать друзей, оформите активную подписку.",
      action: (
        <Link to="/billing">
          <Button size="sm">
            Выбрать тариф <ArrowRight className="h-4 w-4" />
          </Button>
        </Link>
      ),
    },
    disabled: {
      icon: Gift,
      title: "Программа временно недоступна",
      text: "Реферальная программа сейчас отключена администратором.",
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
  const [copied, setCopied] = useState(false);
  const referralLink = `${window.location.origin}/register?ref=${code}`;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(referralLink);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Card className="bg-grain">
      <CardHeader title="Ваша реферальная ссылка" subtitle="Делитесь и получайте награды" />
      <div className="flex items-center gap-2 rounded-xl bg-bg-subtle p-2">
        <code className="flex-1 truncate px-2 text-xs text-fg-muted">{referralLink}</code>
        <Button size="sm" variant="secondary" onClick={handleCopy}>
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          {copied ? "Скопировано" : "Копировать"}
        </Button>
      </div>
    </Card>
  );
}

function StatsRow({ program }: { program: ReferralProgramResponse }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <Card variant="bordered" className="text-center">
        <Users className="mx-auto mb-2 h-5 w-5 text-fg-subtle" />
        <p className="text-2xl font-semibold text-fg">{program.invited_count}</p>
        <p className="text-xs text-fg-subtle">приглашено</p>
      </Card>
      <Card variant="bordered" className="text-center">
        <CreditCard className="mx-auto mb-2 h-5 w-5 text-fg-subtle" />
        <p className="text-2xl font-semibold text-fg">
          {program.invited_with_payment_count}
        </p>
        <p className="text-xs text-fg-subtle">оплатили подписку</p>
      </Card>
    </div>
  );
}

function RewardLevelsCard({ program }: { program: ReferralProgramResponse }) {
  const unit = rewardTypeLabels[program.reward_type] || program.reward_type;

  return (
    <Card variant="bordered">
      <CardHeader title="Уровни вознаграждения" />
      <div className="flex flex-col gap-2">
        {program.reward_levels.map((level) => (
          <div
            key={level.level}
            className="flex items-center justify-between rounded-xl bg-bg-subtle px-3 py-2.5"
          >
            <span className="text-sm text-fg-muted">Уровень {level.level}</span>
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
  const { user } = useAuth();
  const [program, setProgram] = useState<ReferralProgramResponse | null>(null);
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
        setError(e instanceof ApiError ? e.detail : "Не удалось загрузить программу");
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
        <h1 className="text-xl font-semibold text-fg">Реферальная программа</h1>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-fg">Реферальная программа</h1>

      {error && <p className="text-sm text-danger">{error}</p>}
      {unavailableReason && <ReferralUnavailable reason={unavailableReason} />}

      {program && (
        <>
          <ReferralCodeCard code={program.referral_code} />
          <StatsRow program={program} />
          {program.reward_levels.length > 0 && <RewardLevelsCard program={program} />}
        </>
      )}
    </div>
  );
}
