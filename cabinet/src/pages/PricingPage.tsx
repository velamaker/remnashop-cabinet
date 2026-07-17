import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, ArrowRight, Zap, Smartphone, Gauge } from "lucide-react";
import { plansApi } from "@/api/plans";
import type { PublicPlanLandingResponse } from "@/types/api";
import { useBranding } from "@/contexts/BrandingContext";
import { Skeleton } from "@/components/ui/Skeleton";

// Публичная страница тарифов / мини-лендинг (вне входа). Тянет /plans/public,
// кнопки → регистрация. Можно шарить в рекламе (реф-метка ?ref сохраняется).
export default function PricingPage() {
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
  const fmtLimit = (v: number, unit: string) => (v > 0 ? `${v} ${unit}` : "Безлимит");

  return (
    <div className="min-h-screen bg-bg px-4 py-10">
      <div className="mx-auto w-full max-w-5xl">
        {/* Шапка */}
        <div className="flex flex-col items-center text-center">
          {logoUrl && <img src={logoUrl} alt={brandName} className="mb-4 h-14 w-14 rounded-2xl object-cover" />}
          <h1 className="text-3xl font-bold tracking-tight text-fg sm:text-4xl">{brandName || "VPN"}</h1>
          <p className="mt-2 max-w-xl text-sm text-fg-muted sm:text-base">
            Быстрый и надёжный VPN. Выберите тариф — подключение за минуту, оплата в кабинете.
          </p>
        </div>

        {/* Тарифы */}
        <div className="mt-10">
          {error ? (
            <p className="text-center text-sm text-fg-muted">Не удалось загрузить тарифы. Попробуйте позже.</p>
          ) : !plans ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-64 w-full rounded-2xl" />
              ))}
            </div>
          ) : plans.length === 0 ? (
            <p className="text-center text-sm text-fg-muted">Тарифы скоро появятся.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {plans.map((p) => (
                <div
                  key={p.public_code}
                  className="flex flex-col rounded-2xl border border-border-subtle bg-bg-subtle p-5"
                >
                  <h3 className="text-lg font-bold text-fg">{p.name}</h3>
                  {p.description && <p className="mt-1 text-xs text-fg-muted">{p.description}</p>}

                  <div className="mt-4 flex items-baseline gap-1">
                    <span className="text-2xl font-bold text-fg">{p.monthly_from_rub} ₽</span>
                    <span className="text-sm text-fg-muted">/ мес</span>
                  </div>
                  <p className="mt-1 text-xs text-fg-subtle">
                    или {p.max_duration_price_rub} ₽ за {p.max_duration_days} дней
                  </p>

                  <ul className="mt-4 space-y-2 text-sm text-fg-muted">
                    <li className="flex items-center gap-2">
                      <Gauge className="h-4 w-4 text-accent" /> Трафик: {fmtLimit(p.traffic_limit, "ГБ")}
                    </li>
                    <li className="flex items-center gap-2">
                      <Smartphone className="h-4 w-4 text-accent" /> Устройств: {p.device_limit > 0 ? p.device_limit : "Безлимит"}
                    </li>
                    <li className="flex items-center gap-2">
                      <Zap className="h-4 w-4 text-accent" /> Все серверы, без ограничений скорости
                    </li>
                  </ul>

                  <Link
                    to={registerHref}
                    className="btn-gradient mt-5 inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                  >
                    Оформить <ArrowRight className="h-4 w-4" />
                  </Link>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Низ */}
        <div className="mt-10 flex flex-col items-center gap-2 text-sm">
          <Link to={registerHref} className="font-semibold text-accent hover:underline">
            Создать аккаунт
          </Link>
          <span className="text-fg-muted">
            Уже есть аккаунт?{" "}
            <Link to="/login" className="text-accent hover:underline">
              Войти
            </Link>
          </span>
          <Link to="/status" className="mt-1 flex items-center gap-1 text-xs text-fg-subtle hover:text-fg">
            <Check className="h-3.5 w-3.5" /> Статус серверов
          </Link>
        </div>
      </div>
    </div>
  );
}
