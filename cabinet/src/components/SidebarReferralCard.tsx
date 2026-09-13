import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Gift, UserPlus, Coins } from "lucide-react";
import { referralApi, type ReferralEarningsResponse } from "@/api/referral";
import type { ReferralProgramResponse } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

/**
 * Карточка рефералки внизу боковой панели (только десктоп).
 *
 * ЗАЧЕМ. Раздел «Друзья» есть в меню, но выглядит как ещё один пункт списка и
 * теряется. Карточка показывает, что человек УЖЕ заработал, — это и есть повод
 * позвать следующего.
 *
 * ПРО ТЕКСТ. На образце было «получи 15 дней бесплатно», но у нас награда —
 * баллы процентом от платежей приглашённого, а не фиксированные дни. Писать про
 * дни значило бы обещать то, чего программа не даёт, поэтому показываем реальные
 * числа: сколько приглашено и сколько заработано.
 *
 * Подписи взяты из УЖЕ существующих ключей словаря. Новых не заводим: словарь
 * (i18n/dictionaries.ts) — owner-local файл, добавленные туда ключи не уедут в
 * гит, и у всех, кроме этой установки, карточка показывала бы сырые имена
 * ключей вместо текста.
 *
 * КОГДА ПРЯЧЕМСЯ. `/referral/program` отвечает 403, если рефералка выключена или
 * у человека нет активной подписки. В обоих случаях карточку не показываем
 * вовсе: звать друзей, когда программа тебе недоступна, — обман.
 */
export function SidebarReferralCard() {
  const t = useT();
  const [program, setProgram] = useState<ReferralProgramResponse | null>(null);
  const [earnings, setEarnings] = useState<ReferralEarningsResponse | null>(null);

  useEffect(() => {
    let alive = true;
    referralApi
      .program()
      .then((p) => {
        if (alive) setProgram(p);
        // Заработок — необязательная часть: не отдался, покажем только приглашённых.
        return referralApi.earnings().catch(() => null);
      })
      .then((e) => {
        if (alive && e) setEarnings(e);
      })
      .catch(() => {
        /* 403 (выключена / нет подписки) — карточки просто не будет */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!program?.enabled) return null;

  // referral_rewards.amount: для EXTRA_DAYS это дни, иначе рубли — как на
  // странице рефералки, чтобы числа в двух местах не расходились.
  const earnedText =
    earnings && earnings.earned > 0
      ? program.reward_type === "EXTRA_DAYS"
        ? t("ref.earnedDays", { n: earnings.earned })
        : `${earnings.earned.toLocaleString()} ₽`
      : "0";

  return (
    <Link
      to="/referral"
      className="group mx-1 mb-2 block rounded-2xl border border-accent/25 bg-accent-subtle/40 p-3 transition-colors hover:border-accent/50"
    >
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent">
          <Gift className="h-4 w-4" />
        </span>
        <span className="min-w-0 text-sm font-semibold leading-tight text-fg">
          {t("ref.title")}
        </span>
      </div>

      <div className="mt-2.5 space-y-1.5 rounded-xl bg-bg-raised/60 p-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[11px] text-fg-muted">
            <UserPlus className="h-3.5 w-3.5 text-warning" />
            {t("ref.invited")}
          </span>
          <span className="tabular-nums text-sm font-semibold text-fg">
            {program.invited_count}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[11px] text-fg-muted">
            <Coins className="h-3.5 w-3.5 text-success" />
            {t("ref.paid")}
          </span>
          <span className="tabular-nums text-sm font-semibold text-fg">{earnedText}</span>
        </div>
      </div>

      <span className="mt-2.5 flex items-center justify-between rounded-xl border border-[var(--border)] px-2.5 py-1.5 text-xs font-medium text-fg-muted transition-colors group-hover:text-fg">
        {t("nav.referral")}
        <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
