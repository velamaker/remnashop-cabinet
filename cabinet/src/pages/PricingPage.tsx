import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, ArrowRight, ArrowLeft, Zap, Smartphone, Gauge } from "lucide-react";
import { plansApi } from "@/api/plans";
import type { PublicPlanLandingResponse } from "@/types/api";
import { useBranding } from "@/contexts/BrandingContext";
import { Skeleton } from "@/components/ui/Skeleton";
import { useT } from "@/i18n/I18nContext";

// Публичная страница тарифов / мини-лендинг (вне входа). Тянет /plans/public,
// кнопки → регистрация. Можно шарить в рекламе (реф-метка ?ref сохраняется).
export default function PricingPage() {
  const t = useT();
  const { brandName, appearance } = useBranding();
  const logoUrl = appearance?.logo_url || null;
  const [searchParams] = useSearchParams();
  const ref = searchParams.get("ref");
  const [plans, setPlans] = useState<PublicPlanLandingResponse[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    plansApi
      .publicLanding()
      .then((d) => setPlans(d.plans))
      .catch(() => setError(true));
  }, []);

  const registerHref = ref ? `/register?ref=${encodeURIComponent(ref)}` : "/register";
  const fmtLimit = (v: number) => (v > 0 ? `${v} ${t("fmt.gb")}` : t("fmt.unlimited"));

  // Публичная страница (до входа) — не называем сервис впрямую: убираем хвостовой
  // суффикс бренда («Begemot VPN» → «Begemot»). После регистрации «VPN» остаётся.
  const parts = (brandName || "").trim().split(/\s+/);
  const last = parts[parts.length - 1] ?? "";
  const brandMain =
    parts.length > 1 && /^[A-Z0-9]{2,4}$/.test(last) ? parts.slice(0, -1).join(" ") : brandName;

  return (
    <div className="app-scroll h-full bg-bg">
      <div className="mx-auto w-full max-w-5xl px-4 py-8">
        {/* Назад — на лендинг/главную */}
        <Link
          to="/"
          className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-fg-muted transition-colors hover:text-accent"
        >
          <ArrowLeft className="h-4 w-4" /> {t("common.back")}
        </Link>

        {/* Шапка */}
        <div className="flex flex-col items-center text-center">
          {logoUrl && <img src={logoUrl} alt={brandName} className="mb-4 h-14 w-14 rounded-2xl object-cover" />}
          <h1 className="text-3xl font-bold tracking-tight text-fg sm:text-4xl">{brandMain || "Сервис"}</h1>
          <p className="mt-2 max-w-xl text-sm text-fg-muted sm:text-base">
            {t("pricing.subtitle")}
          </p>
        </div>

        {/* Тарифы */}
        <div className="mt-10">
          {error ? (
            <p className="text-center text-sm text-fg-muted">{t("pricing.loadError")}</p>
          ) : !plans ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-64 w-full rounded-2xl" />
              ))}
            </div>
          ) : plans.length === 0 ? (
            <p className="text-center text-sm text-fg-muted">{t("pricing.soon")}</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {plans.map((p) => (
                <div
                  key={p.public_code}
                  className="flex flex-col rounded-2xl border border-border-subtle bg-bg-subtle p-5"
                >
                  {/* Шапка фиксированной высоты — чтобы цены во всех карточках были на одном уровне,
                      даже когда название переносится на две строки («максимум выгоды» и т.п.). */}
                  <div className="min-h-[4.25rem]">
                    <h3 className="text-lg font-bold leading-snug text-fg">{p.name}</h3>
                    {p.description && <p className="mt-1 text-xs text-fg-muted">{p.description}</p>}
                  </div>

                  <div className="mt-4 flex items-baseline gap-1">
                    <span className="text-sm font-medium text-fg-muted">{t("pricing.from")}</span>
                    <span className="text-2xl font-bold text-fg">{Math.round(Number(p.monthly_from_rub))} ₽</span>
                    <span className="text-sm text-fg-muted">{t("pricing.perMonth")}</span>
                  </div>
                  <p className="mt-1 text-xs text-fg-subtle">
                    {t("pricing.annualNote", { days: p.max_duration_days, price: p.max_duration_price_rub })}
                  </p>

                  <ul className="mt-4 space-y-2 text-sm text-fg-muted">
                    <li className="flex items-center gap-2">
                      <Gauge className="h-4 w-4 text-accent" /> {t("pricing.traffic")}: {fmtLimit(p.traffic_limit)}
                    </li>
                    <li className="flex items-center gap-2">
                      <Smartphone className="h-4 w-4 text-accent" /> {t("pricing.devices")}: {p.device_limit > 0 ? p.device_limit : t("fmt.unlimited")}
                    </li>
                    <li className="flex items-center gap-2">
                      <Zap className="h-4 w-4 text-accent" /> {t("pricing.allServers")}
                    </li>
                  </ul>

                  <Link
                    to={registerHref}
                    className="btn-gradient mt-6 inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                  >
                    {t("pricing.subscribe")} <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Низ */}
        <div className="mt-10 flex flex-col items-center gap-2 text-sm">
          <Link to={registerHref} className="font-semibold text-accent hover:underline">
            {t("register.title")}
          </Link>
          <span className="text-fg-muted">
            {t("pricing.haveAccount")}{" "}
            <Link to="/login" className="text-accent hover:underline">
              {t("login.submit")}
            </Link>
          </span>
          <Link to="/status" className="mt-1 flex items-center gap-1 text-xs text-fg-subtle hover:text-fg">
            <Check className="h-3.5 w-3.5" /> {t("status.title")}
          </Link>
        </div>
      </div>
    </div>
  );
}
