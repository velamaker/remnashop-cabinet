import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, ArrowRight, ArrowLeft, Zap, Smartphone, Gauge, Sparkles } from "lucide-react";
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
      <div className="relative mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
        <div aria-hidden className="ambient-glow left-1/2 top-0 h-[24rem] w-[24rem] -translate-x-1/2" />

        <Link
          to="/"
          className="relative mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-fg-muted transition-colors hover:text-accent"
        >
          <ArrowLeft className="h-4 w-4" /> {t("common.back")}
        </Link>

        <section className="relative overflow-hidden rounded-[28px] border border-[var(--border)] bg-bg-raised/90 p-6 shadow-[0_30px_70px_-32px_rgba(0,0,0,0.55)] sm:p-8 lg:p-10">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(77,139,255,0.18),transparent_48%)]" />
          <div className="relative flex flex-col items-center text-center">
            {logoUrl ? (
              <img src={logoUrl} alt={brandName} className="mb-4 h-14 w-14 rounded-2xl object-cover ring-1 ring-[color:var(--border)]" />
            ) : (
              <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-[color:var(--border)] bg-accent-subtle text-accent">
                <Sparkles className="h-7 w-7" />
              </div>
            )}
            <h1 className="text-3xl font-semibold tracking-[-0.02em] text-fg sm:text-4xl">{brandMain || "Сервис"}</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-fg-muted sm:text-base">
              {t("pricing.subtitle")}
            </p>
          </div>

          <div className="relative mt-10">
            {error ? (
              <p className="text-center text-sm text-fg-muted">{t("pricing.loadError")}</p>
            ) : !plans ? (
              <div className="grid gap-4 lg:grid-cols-3">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-72 w-full rounded-[24px]" />
                ))}
              </div>
            ) : plans.length === 0 ? (
              <p className="text-center text-sm text-fg-muted">{t("pricing.soon")}</p>
            ) : (
              <div className="grid gap-4 lg:grid-cols-3">
                {plans.map((p, index) => {
                  const featured = index === 1;
                  return (
                    <article
                      key={p.public_code}
                      className={`group relative flex flex-col rounded-[24px] border p-5 transition-all duration-200 ${featured ? "border-accent/40 bg-gradient-to-br from-accent-subtle/70 via-bg-raised to-bg-subtle shadow-[0_20px_50px_-24px_var(--accent-glow)]" : "border-[color:var(--border)] bg-bg-subtle/70 hover:border-[color:var(--accent)]/40"}`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-h-[4.25rem]">
                          <h3 className="text-lg font-semibold leading-snug text-fg">{p.name}</h3>
                          {p.description && <p className="mt-1 text-sm leading-5 text-fg-muted">{p.description}</p>}
                        </div>
                        {featured && (
                          <span className="inline-flex shrink-0 items-center rounded-full border border-accent/30 bg-accent-subtle px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">
                            Popular
                          </span>
                        )}
                      </div>

                      <div className="mt-5 flex items-end gap-2">
                        <span className="text-sm font-medium text-fg-muted">{t("pricing.from")}</span>
                        <span className="text-3xl font-semibold tracking-[-0.02em] text-fg">{Math.round(Number(p.monthly_from_rub))} ₽</span>
                        <span className="pb-1 text-sm text-fg-muted">{t("pricing.perMonth")}</span>
                      </div>
                      <p className="mt-2 text-sm text-fg-subtle">
                        {t("pricing.annualNote", { days: p.max_duration_days, price: p.max_duration_price_rub })}
                      </p>

                      <ul className="mt-5 space-y-2.5 text-sm text-fg-muted">
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
                        className="btn-gradient mt-6 inline-flex items-center justify-center gap-1.5 rounded-2xl px-4 py-2.5 text-sm font-semibold text-white"
                      >
                        {t("pricing.subscribe")} <ArrowRight className="h-4 w-4" />
                      </Link>
                    </article>
                  );
                })}
              </div>
            )}
          </div>

          <div className="relative mt-10 flex flex-col items-center gap-2 text-sm">
            <Link to={registerHref} className="font-semibold text-accent hover:underline">
              {t("register.title")}
            </Link>
            <span className="text-fg-muted">
              {t("pricing.haveAccount")} {" "}
              <Link to="/login" className="text-accent hover:underline">
                {t("login.submit")}
              </Link>
            </span>
            <Link to="/status" className="mt-1 flex items-center gap-1 text-xs text-fg-subtle hover:text-fg">
              <Check className="h-3.5 w-3.5" /> {t("status.title")}
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
