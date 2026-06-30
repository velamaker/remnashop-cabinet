import { type ReactNode, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { clsx } from "clsx";
import {
  LayoutDashboard,
  Users,
  CreditCard,
  Tag,
  LogOut,
  ChevronLeft,
  Package,
  Radio,
  Settings,
  Wallet,
  Link2,
  Waves,
  LifeBuoy,
  Palette,
  ShieldAlert,
  Smartphone,
  SquareMenu,
  Mail,
  Info,
  Menu,
  X,
  Eye,
  KeyRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";

type NavItem = { to: string; icon: LucideIcon; label: string; end?: boolean };

// Разделы сгруппированы по категориям (заголовки в сайдбаре), чтобы длинный
// список не висел плоской простынёй.
const navGroups: { title: string; items: NavItem[] }[] = [
  {
    title: "Аналитика",
    items: [
      { to: "/admin", icon: LayoutDashboard, label: "Обзор", end: true },
      { to: "/admin/transactions", icon: CreditCard, label: "Транзакции" },
    ],
  },
  {
    title: "Пользователи",
    items: [
      { to: "/admin/users", icon: Users, label: "Пользователи" },
      { to: "/admin/support", icon: LifeBuoy, label: "Поддержка" },
    ],
  },
  {
    title: "Продажи",
    items: [
      { to: "/admin/plans", icon: Package, label: "Тарифы" },
      { to: "/admin/promocodes", icon: Tag, label: "Промокоды" },
      { to: "/admin/gateways", icon: Wallet, label: "Шлюзы" },
    ],
  },
  {
    title: "Маркетинг",
    items: [
      { to: "/admin/ad-links", icon: Link2, label: "Рекл. ссылки" },
      { to: "/admin/broadcasts", icon: Radio, label: "Рассылки" },
    ],
  },
  {
    title: "Кабинет",
    items: [
      { to: "/admin/appearance", icon: Palette, label: "Оформление" },
      { to: "/admin/info", icon: Info, label: "Информация" },
      { to: "/admin/menu", icon: SquareMenu, label: "Меню" },
      { to: "/admin/apps", icon: Smartphone, label: "Приложения" },
      { to: "/admin/email", icon: Mail, label: "Письмо" },
    ],
  },
  {
    title: "Система",
    items: [
      { to: "/admin/remnawave", icon: Waves, label: "RemnaWave" },
      { to: "/admin/auth", icon: KeyRound, label: "Вход через Telegram" },
      { to: "/admin/settings", icon: Settings, label: "Настройки" },
      { to: "/admin/audit", icon: ShieldAlert, label: "Аудит" },
    ],
  },
];

// Плоский список для мобильного нижнего навбара (там без группировки).
const navItems = navGroups.flatMap((g) => g.items);

function GroupedNav({ onNavigate, itemPad }: { onNavigate?: () => void; itemPad: string }) {
  return (
    <>
      {navGroups.map((group) => (
        <div key={group.title} className="flex flex-col gap-0.5">
          <span className="px-2.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
            {group.title}
          </span>
          {group.items.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onNavigate}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2.5 rounded-lg px-2.5 text-sm transition-colors duration-150",
                  itemPad,
                  isActive
                    ? "bg-bg-raised font-medium text-fg"
                    : "font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )
              }
            >
              <Icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </div>
      ))}
    </>
  );
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const { user, logout, isReadonlyAdmin } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="relative flex w-full min-h-[100dvh] overflow-x-hidden bg-bg">
      {/* Sidebar */}
      <aside className="hidden w-52 flex-shrink-0 flex-col border-r border-[var(--border)] bg-bg px-2 py-5 md:flex sticky top-0 h-screen overflow-hidden">
        <div className="mb-6 flex items-center gap-2 px-2.5">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-danger/10 text-danger">
            <span className="text-[11px] font-bold tracking-tight">A</span>
          </div>
          <span className="text-sm font-semibold tracking-tight text-fg">Админ</span>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
          <GroupedNav itemPad="py-2" />
        </nav>

        <div className="mt-4 flex flex-col gap-0.5 border-t border-[var(--border)] pt-4">
          <NavLink
            to="/"
            className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-normal text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <ChevronLeft className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            Кабинет
          </NavLink>
          <div className="mt-2 flex items-center justify-between px-2.5">
            <span className="truncate text-xs text-fg-subtle">
              {user?.username ? `@${user.username}` : user?.email || user?.name}
            </span>
            <ThemeSwitcher />
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
      <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-[var(--border)] bg-bg/90 px-4 py-3 backdrop-blur-md md:hidden">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label="Меню админки"
            className="-ml-1 flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <Menu className="h-5 w-5" strokeWidth={1.75} />
          </button>
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-danger/10 text-danger">
            <span className="text-[11px] font-bold">A</span>
          </div>
          <span className="text-sm font-semibold tracking-tight text-fg">Админ</span>
        </div>
        <ThemeSwitcher />
      </div>

      {/* Mobile menu drawer — все разделы (не помещаются в нижний навбар) */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-black/40 animate-fade-in"
            onClick={() => setMenuOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-72 max-w-[82%] flex-col overflow-y-auto border-r border-[var(--border)] bg-bg px-2 py-4">
            <div className="mb-4 flex items-center justify-between px-2.5">
              <div className="flex items-center gap-2">
                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-danger/10 text-danger">
                  <span className="text-[11px] font-bold">A</span>
                </div>
                <span className="text-sm font-semibold tracking-tight text-fg">Админ</span>
              </div>
              <button
                onClick={() => setMenuOpen(false)}
                aria-label="Закрыть"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted hover:bg-bg-subtle hover:text-fg"
              >
                <X className="h-5 w-5" strokeWidth={1.75} />
              </button>
            </div>

            <nav className="flex flex-1 flex-col gap-0.5">
              <GroupedNav onNavigate={() => setMenuOpen(false)} itemPad="py-2.5" />
            </nav>

            <div className="mt-4 flex flex-col gap-0.5 border-t border-[var(--border)] pt-4">
              <NavLink
                to="/"
                onClick={() => setMenuOpen(false)}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-sm font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg"
              >
                <ChevronLeft className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
                Кабинет
              </NavLink>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-sm font-normal text-fg-muted hover:bg-danger/8 hover:text-danger"
              >
                <LogOut className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
                Выйти
              </button>
            </div>
          </aside>
        </div>
      )}

      {/* Main */}
      <main className="flex-1 min-w-0 overflow-x-hidden px-5 pb-24 pt-20 md:px-8 md:pb-8 md:pt-8">
        <div className="mx-auto max-w-6xl animate-fade-in">
          {isReadonlyAdmin && (
            <div className="mb-5 flex items-center gap-2.5 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
              <Eye className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
              <span>
                <strong className="font-semibold">Режим просмотра.</strong>{" "}
                Вам доступна вся админка, но изменения отключены — кнопки сохранения
                и действия не сработают.
              </span>
            </div>
          )}
          {children}
        </div>
      </main>

      {/* Mobile bottom nav — горизонтальная прокрутка по всем разделам */}
      <nav className="scrollbar-hide fixed inset-x-0 bottom-0 z-20 flex items-center gap-0.5 overflow-x-auto border-t border-[var(--border)] bg-bg/90 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-md md:hidden">
        {navItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              clsx(
                "flex min-w-[60px] shrink-0 flex-col items-center gap-1 rounded-lg px-1 py-1.5 text-[10px] font-medium transition-colors",
                isActive ? "text-accent" : "text-fg-subtle",
              )
            }
          >
            <Icon className="h-5 w-5" strokeWidth={1.75} />
            <span className="max-w-[64px] truncate">{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
