import { type ReactNode, useEffect } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  Smartphone,
  CreditCard,
  Gift,
  Settings,
  LogOut,
  Info,
  ShieldCheck,
  LifeBuoy,
  Wallet,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useT } from "@/i18n/I18nContext";
import { BrandWordmark } from "@/components/BrandWordmark";
import { BrandLogo } from "@/components/BrandLogo";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { NotificationBell } from "@/components/NotificationBell";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { getTelegramWebApp } from "@/hooks/useTelegramWebApp";

const navItems = [
  { to: "/", icon: LayoutDashboard, labelKey: "nav.home" },
  { to: "/subscription", icon: CreditCard, labelKey: "nav.subscription" },
  { to: "/balance", icon: Wallet, labelKey: "nav.balance" },
  { to: "/devices", icon: Smartphone, labelKey: "nav.devices" },
  { to: "/referral", icon: Gift, labelKey: "nav.referral" },
  { to: "/support", icon: LifeBuoy, labelKey: "nav.support" },
  { to: "/settings", icon: Settings, labelKey: "nav.settings" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const { user, isAdmin, logout } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;
    app.expand();
  }, []);

  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;
    if (location.pathname !== "/") {
      app.BackButton.show();
      const handler = () => navigate(-1);
      app.BackButton.onClick(handler);
      return () => {
        app.BackButton.offClick(handler);
        app.BackButton.hide();
      };
    } else {
      app.BackButton.hide();
    }
  }, [location.pathname, navigate]);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const displayName = user?.username
    ? `@${user.username}`
    : user?.email || user?.name || t("set.profile");

  return (
    <div className="relative flex w-full h-[100dvh] overflow-hidden bg-bg">
      {/* Ambient background glow — single subtle accent glow (top-left) */}
      <div aria-hidden className="ambient-glow -left-32 -top-40 h-96 w-96" />

      {/* Sidebar — fixed to viewport height */}
      <aside className="relative z-10 hidden w-52 flex-shrink-0 flex-col border-r border-[var(--border)] bg-bg/70 px-2 py-5 backdrop-blur-xl md:flex sticky top-0 h-screen overflow-hidden">
        {/* Brand — клик возвращает на главную */}
        <NavLink to="/" end className="mb-7 flex items-center gap-3 rounded-xl px-2 py-1 transition-opacity hover:opacity-80">
          <BrandLogo size={42} />
          <BrandWordmark className="text-[20px] leading-none" />
        </NavLink>

        {/* Nav */}
        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
          {navItems.map(({ to, icon: Icon, labelKey }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-all duration-150",
                  isActive
                    ? "nav-active-glow font-medium text-fg"
                    : "font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )
              }
            >
              <Icon
                className={clsx("h-4 w-4 flex-shrink-0 transition-colors")}
                strokeWidth={1.75}
              />
              {t(labelKey)}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="mt-4 flex flex-col gap-0.5 border-t border-[var(--border)] pt-4">
          {/* Admin link — только для админов (ADMIN/DEV/OWNER), fail-closed */}
          {isAdmin && (
            <NavLink
              to="/admin"
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors duration-150",
                  isActive
                    ? "bg-danger/8 font-medium text-danger"
                    : "font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )
              }
            >
              <ShieldCheck className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
              Админ панель
            </NavLink>
          )}
          <NavLink
            to="/info"
            className={({ isActive }) =>
              clsx(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors duration-150",
                isActive
                  ? "bg-bg-raised font-medium text-fg"
                  : "font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg",
              )
            }
          >
            <Info className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            {t("nav.info")}
          </NavLink>

          <div className="mt-2 px-2.5">
            <span className="block break-all text-xs leading-snug text-fg-subtle">{displayName}</span>
          </div>

          <button
            onClick={handleLogout}
            className="mt-1 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-normal text-fg-muted transition-colors hover:bg-danger/8 hover:text-danger"
          >
            <LogOut className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            {t("nav.logout")}
          </button>
        </div>
      </aside>

      {/* Mobile top bar — pt учитывает safe-area (в PWA standalone контент уходит
          под статус-бар/вырез iOS из-за viewport-fit=cover) */}
      <div className="fixed inset-x-0 top-0 z-30 flex items-center justify-between border-b border-[var(--border)] bg-bg px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] md:hidden">
        <NavLink to="/" end className="flex items-center gap-2 transition-opacity active:opacity-70">
          <BrandLogo size={28} className="!rounded-lg" />
          <BrandWordmark className="text-sm" />
        </NavLink>
        <div className="flex items-center gap-1">
          {/* Вход в админку — только для админов (на мобиле другого входа нет) */}
          {isAdmin && (
            <NavLink
              to="/admin"
              aria-label={t("common.adminPanel")}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-danger transition-colors hover:bg-danger/10 active:opacity-70"
            >
              <ShieldCheck className="h-5 w-5" strokeWidth={2} />
            </NavLink>
          )}
          <NotificationBell />
          <LanguageSwitcher />
          <ThemeSwitcher />
        </div>
      </div>

      {/* Тема + язык — вверху справа (десктоп) */}
      <div className="fixed right-6 top-5 z-30 hidden items-center gap-2 md:flex">
        <NotificationBell />
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>

      {/* Main content — единственный скролл-контейнер страницы (app-scroll) */}
      <main className="app-scroll relative z-10 flex-1 min-w-0 px-5 pb-28 pt-[calc(5rem+env(safe-area-inset-top))] md:px-8 md:pb-8 md:pt-8">
        {/* key={pathname} → обёртка перемонтируется на смене роута и заново
            проигрывает fade-in (плавное появление каждой страницы, не только первой) */}
        <div key={location.pathname} className="mx-auto max-w-4xl animate-fade-in">{children}</div>
      </main>

      {/* Mobile bottom nav — без «Устройства» и «Рефералка» (доступны с Главной/Подписки) */}
      <nav className="fixed inset-x-0 bottom-0 z-30 flex items-center justify-around border-t border-[var(--border)] bg-bg px-2 pb-2 pt-2 shadow-[0_-4px_16px_-8px_rgba(0,0,0,0.3)] md:hidden">
        {navItems
          .filter(({ to }) => to !== "/devices" && to !== "/referral")
          .map(({ to, icon: Icon, labelKey }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              clsx(
                "flex flex-1 flex-col items-center gap-1 rounded-lg py-1.5 text-[10px] font-medium transition-colors",
                isActive ? "text-accent" : "text-fg-subtle",
              )
            }
          >
            <Icon className="h-5 w-5" strokeWidth={1.75} />
            {t(labelKey)}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
