import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Gift, ArrowRight, Timer } from "lucide-react";
import { trialDiscountApi, type TrialDiscountStatus } from "@/api/trialDiscount";
import { useT } from "@/i18n/I18nContext";

/** Форматирует оставшееся время в «Дд ЧЧ:ММ:СС» / «ЧЧ:ММ:СС». */
function fmtLeft(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return d > 0 ? `${d}д ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
}

/**
 * Баннер-таймер «скидка на первую покупку триальщику». Сам ходит за статусом
 * (/trial-discount) и показывается только если есть активная скидка. Пропадает,
 * когда таймер истёк или скидку использовали (бэкенд вернёт active=false).
 */
export function TrialDiscountBanner() {
  const t = useT();
  const [data, setData] = useState<TrialDiscountStatus | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    trialDiscountApi
      .get()
      .then((d) => {
        if (alive) setData(d);
      })
      .catch(() => {
        /* тихо: баннер необязательный */
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!data?.active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [data?.active]);

  if (!data?.active || !data.percent) return null;

  const expiresMs = data.expires_at ? new Date(data.expires_at).getTime() : 0;
  const left = expiresMs - now;
  if (expiresMs && left <= 0) return null; // таймер вышел — прячем

  return (
    <div className="overflow-hidden rounded-2xl border border-accent/40 bg-gradient-to-br from-accent/15 to-accent-2/15 p-4 sm:p-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-accent">
          <Gift className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-bold text-fg">
            {t("trialDiscount.title", { percent: data.percent })}
          </p>
          <p className="mt-0.5 text-xs text-fg-muted sm:text-sm">{t("trialDiscount.subtitle")}</p>
          {expiresMs > 0 && (
            <p className="mt-1 flex items-center gap-1 text-xs font-semibold text-accent">
              <Timer className="h-3.5 w-3.5" />
              {t("trialDiscount.timeLeft", { time: fmtLeft(left) })}
            </p>
          )}
        </div>
        <Link
          to="/billing"
          className="btn-gradient inline-flex shrink-0 items-center gap-1 rounded-xl px-3 py-2 text-sm font-semibold text-white"
        >
          {t("trialDiscount.cta")}
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </div>
  );
}
