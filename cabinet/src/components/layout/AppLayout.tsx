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
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { BrandWordmark } from "@/components/BrandWordmark";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { getTelegramWebApp } from "@/hooks/useTelegramWebApp";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Главная", fullLabel: "Главная" },
  { to: "/subscription", icon: CreditCard, label: "Подписка", fullLabel: "Подписка" },
  { to: "/devices", icon: Smartphone, label: "Устройства", fullLabel: "Устройства" },
  { to: "/referral", icon: Gift, label: "Рефералка", fullLabel: "Рефералка" },
  { to: "/support", icon: LifeBuoy, label: "Поддержка", fullLabel: "Поддержка" },
  { to: "/settings", icon: Settings, label: "Настройки", fullLabel: "Настройки" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const { user, isAdmin, logout } = useAuth();
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
    : user?.email || user?.name || "Профиль";

  return (
    <div className="relative flex w-full min-h-[100dvh] overflow-x-hidden bg-bg">
      {/* Ambient background glow — single subtle accent glow (top-left) */}
      <div aria-hidden className="ambient-glow -left-32 -top-40 h-96 w-96" />

      {/* Sidebar — fixed to viewport height */}
      <aside className="relative z-10 hidden w-52 flex-shrink-0 flex-col border-r border-[var(--border)] bg-bg/70 px-2 py-5 backdrop-blur-xl md:flex sticky top-0 h-screen overflow-hidden">
        {/* Brand — клик возвращает на главную */}
        <NavLink to="/" end className="mb-7 flex items-center gap-3 rounded-xl px-2 py-1 transition-opacity hover:opacity-80">
          <div className="brand-mark flex h-[42px] w-[42px] items-center justify-center rounded-2xl text-white">
            <ShieldCheck className="h-[23px] w-[23px]" strokeWidth={2.2} />
          </div>
          <BrandWordmark className="text-[20px] leading-none" />
        </NavLink>

        {/* Nav */}
        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
          {navItems.map(({ to, icon: Icon, fullLabel }) => (
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
              {fullLabel}
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
            Информация
          </NavLink>

          <div className="mt-2 px-2.5">
            <span className="block break-all text-xs leading-snug text-fg-subtle">{displayName}</span>
          </div>

          <button
            onClick={handleLogout}
            className="mt-1 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-normal text-fg-muted transition-colors hover:bg-danger/8 hover:text-danger"
          >
            <LogOut className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            Выйти
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-[var(--border)] bg-bg/80 px-4 py-3 backdrop-blur-xl md:hidden">
        <NavLink to="/" end className="flex items-center gap-2 transition-opacity active:opacity-70">
          <div className="brand-mark flex h-7 w-7 items-center justify-center rounded-lg text-white">
            <ShieldCheck className="h-4 w-4" strokeWidth={2.2} />
          </div>
          <BrandWordmark className="text-sm" />
        </NavLink>
        <ThemeSwitcher />
      </div>

      {/* Переключатель темы — вверху справа (десктоп) */}
      <div className="fixed right-6 top-5 z-30 hidden md:block">
        <ThemeSwitcher />
      </div>

      {/* Main content */}
      <main className="relative z-10 flex-1 min-w-0 px-5 pb-28 pt-20 md:px-8 md:pb-8 md:pt-8">
        <div className="mx-auto max-w-4xl animate-fade-in">{children}</div>
      </main>

      {/* Mobile bottom nav */}
      <nav className="fixed inset-x-0 bottom-0 z-20 flex items-center justify-around border-t border-[var(--border)] bg-bg/90 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-md md:hidden">
        {navItems.map(({ to, icon: Icon, label }) => (
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
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
