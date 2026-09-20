import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Wallet } from "lucide-react";
import { balanceApi, type BalanceResponse } from "@/api/balance";
import { activeLocale } from "@/lib/format";
import { useT } from "@/i18n/I18nContext";

/**
 * Автопродление подписки с ₽-баланса.
 *
 * ЗАЧЕМ ОТДЕЛЬНАЯ КАРТОЧКА. Тумблер жил только на «Балансе», ниже пополнения и
 * истории операций. Замер на бою: автопродление включено у 0 человек из 1124, при
 * том что крон исправно ходит каждый час. Ноль из тысячи — это не «не хотят», а
 * «не нашли»: решение продлевать принимают на странице подписки, а тумблер лежал
 * через две вкладки от неё.
 *
 * ЧЕСТНОСТЬ ВАЖНЕЕ ВКЛЮЧЕНИЙ. Списываем с РУБЛЁВОГО баланса, а он больше нуля у
 * троих человек. Поэтому карточка всегда называет остаток и, если списывать нечего,
 * говорит это прямо и ведёт пополнить — иначе человек включит тумблер и будет
 * уверен, что подписка продлится сама.
 *
 * Срок списания приходит с бэкенда (`autopay_days_before`): он живёт в переменной
 * окружения крона, и на чужой установке может быть не 3. Старый бот поля не шлёт —
 * тогда говорим без числа, а не выдумываем своё.
 */

interface Props {
  /** Уже загруженные данные (страница «Баланс»). Без них карточка грузит сама. */
  data?: BalanceResponse | null;
  onChange?: (enabled: boolean) => void;
}

export function AutopayCard({ data, onChange }: Props) {
  const t = useT();
  const [own, setOwn] = useState<BalanceResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const external = data !== undefined;
  const info = external ? data : own;

  useEffect(() => {
    if (external) return;
    let alive = true;
    balanceApi
      .get()
      .then((r) => alive && setOwn(r))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [external]);

  if (failed || !info) return null;

  const enabled = info.autopay_enabled;
  const balance = info.balance ?? 0;
  const days = info.autopay_days_before;
  // Цену продления считает бэкенд той же функцией, что и само списание. Нет её
  // (старый бот, тариф снят с продажи, нет ₽-шлюза) — говорим общими словами.
  const price = info.autopay_price ?? null;
  const short = price !== null && price > balance ? price - balance : 0;
  const empty = price !== null ? short > 0 : balance <= 0;

  const toggle = async () => {
    setBusy(true);
    try {
      const r = await balanceApi.setAutopay(!enabled);
      setOwn((prev) => (prev ? { ...prev, autopay_enabled: r.autopay_enabled } : prev));
      onChange?.(r.autopay_enabled);
    } catch {
      /* молча: тумблер останется в прежнем положении */
    } finally {
      setBusy(false);
    }
  };

  // Что обещаем: с числом дней и с суммой списания, если бот их прислал.
  const money = (v: number) => v.toLocaleString(activeLocale(), { maximumFractionDigits: 2 });
  const sum = money(balance);
  const promise =
    price !== null && days
      ? t("autopay.willChargeSum", { days, sum: money(price), balance: sum })
      : days
        ? t("autopay.willCharge", { days, sum })
        : t("autopay.willChargeNoDays", { sum });

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-fg">{t("autopay.title")}</p>
            <p className="mt-0.5 text-xs text-fg-muted">{enabled ? promise : t("autopay.off")}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          role="switch"
          aria-checked={enabled}
          aria-label={t("autopay.title")}
          className={`relative h-6 w-11 flex-shrink-0 rounded-full transition-colors disabled:opacity-50 ${enabled ? "bg-accent" : "bg-bg-overlay border border-[var(--border)]"}`}
        >
          <span
            className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${enabled ? "left-[22px]" : "left-0.5"}`}
          />
        </button>
      </div>

      {enabled && empty && (
        <div className="mt-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2">
          <p className="text-xs text-fg">
            {short > 0 ? t("autopay.short", { short: money(short) }) : t("autopay.emptyBalance")}
          </p>
          {/* Пополняем сразу на недостающую сумму: поле в «Балансе» откроется
              заполненным, человеку остаётся выбрать способ оплаты. */}
          <Link
            to={short > 0 ? `/balance?topup=${Math.ceil(short)}` : "/balance"}
            className="mt-1.5 inline-flex items-center gap-1.5 text-xs font-semibold text-accent hover:underline"
          >
            <Wallet className="h-3.5 w-3.5" />
            {short > 0 ? t("autopay.topupSum", { short: money(Math.ceil(short)) }) : t("autopay.topup")}
          </Link>
        </div>
      )}
    </div>
  );
}
