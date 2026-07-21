import { type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Wrench } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { BrandLogo } from "@/components/BrandLogo";

/**
 * Режим тех-работ. Когда в оформлении включён `maintenance` (свой тумблер или синхрон
 * с режимом доступа бота) И отмечена галка «ограничивать вход» (по умолчанию — да),
 * для НЕ-админов кабинет полностью закрыт: показывается заглушка. Админ входит через
 * скрытую ссылку `/login?staff=1` (обычным пользователям форму входа не показываем),
 * после чего получает полный доступ, чтобы выключить режим. Если галка «вход» снята,
 * кабинет открыт, а регистрация/оплата ограничиваются точечно на своих страницах.
 */
export function MaintenanceGate({ children }: { children: ReactNode }) {
  const { appearance } = useBranding();
  const { isAdmin, isLoading } = useAuth();
  const { pathname, search } = useLocation();

  // Полное закрытие — только если тех-работы включены И ограничен вход (дефолт true).
  const maintenance =
    appearance?.maintenance === true && appearance?.maintenance_block_login !== false;
  const staffLogin =
    pathname === "/login" && new URLSearchParams(search).get("staff") === "1";

  if (!maintenance || isAdmin || isLoading || staffLogin) {
    return <>{children}</>;
  }

  const msg = appearance?.maintenance_message?.trim();
  return (
    <div className="app-scroll flex min-h-[100dvh] flex-col items-center justify-center gap-5 bg-bg px-6 text-center">
      <BrandLogo size={56} />
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-warning/15 text-warning">
        <Wrench className="h-6 w-6" />
      </div>
      <div>
        <h1 className="text-xl font-bold text-fg">Идут технические работы</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-fg-muted">
          {msg || "Кабинет временно недоступен. Пожалуйста, зайдите позже."}
        </p>
      </div>
      <Link to="/login?staff=1" className="mt-2 text-xs text-fg-subtle transition-colors hover:text-accent">
        Вход для администратора
      </Link>
    </div>
  );
}
