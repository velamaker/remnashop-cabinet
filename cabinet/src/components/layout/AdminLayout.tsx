import { type ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
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
  Sparkles,
  Fingerprint,
  Route as RouteIcon,
  DownloadCloud,
  Gift,
  Coins,
  Sunrise,
  Activity,
  PanelLeftClose,
  PanelLeftOpen,
  Bell,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";

// section — ключ раздела прав (см. backend permissions.py). Пункт показывается,
// только если у пользователя есть доступ к разделу (fullAccess или в списке).
export type NavItem = { to: string; icon: LucideIcon; label: string; end?: boolean; section: string };

// Разделы сгруппированы по категориям (заголовки в сайдбаре), чтобы длинный
// список не висел плоской простынёй. Экспортируется — тот же список рисуется
// плиточным лаунчером на главной админки (Обзор).
export const navGroups: { title: string; items: NavItem[] }[] = [
  {
    title: "Аналитика",
    items: [
      { to: "/admin/stats", icon: LayoutDashboard, label: "Статистика", section: "dashboard" },
      { to: "/admin/transactions", icon: CreditCard, label: "Транзакции", section: "transactions" },
    ],
  },
  {
    title: "Пользователи",
    items: [
      { to: "/admin/users", icon: Users, label: "Пользователи", section: "users" },
      { to: "/admin/referral", icon: Gift, label: "Рефералы", section: "settings" },
      { to: "/admin/import", icon: DownloadCloud, label: "Импорт", section: "import" },
      { to: "/admin/abuse", icon: Fingerprint, label: "Детект абьюза", section: "abuse" },
      { to: "/admin/support", icon: LifeBuoy, label: "Поддержка", section: "support" },
    ],
  },
  {
    title: "Продажи",
    items: [
      { to: "/admin/plans", icon: Package, label: "Тарифы", section: "plans" },
      { to: "/admin/promocodes", icon: Tag, label: "Промокоды", section: "promocodes" },
      { to: "/admin/gateways", icon: Wallet, label: "Шлюзы", section: "gateways" },
      { to: "/admin/topup", icon: Coins, label: "Пополнение", section: "settings" },
    ],
  },
  {
    title: "Маркетинг",
    items: [
      { to: "/admin/ad-links", icon: Link2, label: "Рекл. ссылки", section: "ad_links" },
      { to: "/admin/broadcasts", icon: Radio, label: "Рассылки", section: "broadcasts" },
    ],
  },
  {
    title: "Кабинет",
    items: [
      { to: "/admin/appearance", icon: Palette, label: "Оформление", section: "content" },
      { to: "/admin/info", icon: Info, label: "Информация", section: "content" },
      { to: "/admin/menu", icon: SquareMenu, label: "Меню", section: "content" },
      { to: "/admin/apps", icon: Smartphone, label: "Приложения", section: "content" },
      {
        to: "/admin/subscription-app",
        icon: RouteIcon,
        label: "Подписка в прилож.",
        section: "settings",
      },
      { to: "/admin/server-status", icon: Activity, label: "Статус сервиса", section: "settings" },
      { to: "/admin/email", icon: Mail, label: "Письмо", section: "settings" },
    ],
  },
  {
    title: "Система",
    items: [
      { to: "/admin/remnawave", icon: Waves, label: "RemnaWave", section: "remnawave" },
      { to: "/admin/auth", icon: KeyRound, label: "Вход через Telegram", section: "settings" },
      { to: "/admin/settings", icon: Settings, label: "Настройки", section: "settings" },
      { to: "/admin/notifications", icon: Bell, label: "Уведомления", section: "settings" },
      { to: "/admin/summary", icon: Sunrise, label: "Утренняя сводка", section: "settings" },
      { to: "/admin/audit", icon: ShieldAlert, label: "Аудит", section: "audit" },
      { to: "/admin/updates", icon: Sparkles, label: "Обновления", section: "updates" },
    ],
  },
];

function GroupedNav({
  onNavigate,
  itemPad,
  canSection,
  collapsed = false,
}: {
  onNavigate?: () => void;
  itemPad: string;
  canSection: (key: string) => boolean;
  collapsed?: boolean;
}) {
  const groups = navGroups
    .map((group) => ({ ...group, items: group.items.filter((it) => canSection(it.section)) }))
    .filter((group) => group.items.length > 0);
  return (
    <>
      {groups.map((group) => (
        <div key={group.title} className="flex flex-col gap-0.5">
          {collapsed ? (
            <span className="mx-auto my-1.5 h-px w-6 bg-[var(--border)]" />
          ) : (
            <span className="px-2.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
              {group.title}
            </span>
          )}
          {group.items.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onNavigate}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                clsx(
                  "flex items-center rounded-lg text-sm transition-colors duration-150",
                  collapsed ? "justify-center px-0 py-2" : "gap-2.5 px-2.5",
                  itemPad,
                  isActive
                    ? "bg-bg-raised font-medium text-fg"
                    : "font-normal text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )
              }
            >
              <Icon className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
              {!collapsed && label}
            </NavLink>
          ))}
        </div>
      ))}
    </>
  );
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const { user, logout, isReadonlyAdmin, canSection } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const drawerNavRef = useRef<HTMLElement>(null);
  // При открытии меню — скролл к активному пункту (а не всегда наверх).
  useEffect(() => {
    if (!menuOpen) return;
    const t = setTimeout(() => {
      drawerNavRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "center" });
    }, 0);
    return () => clearTimeout(t);
  }, [menuOpen]);
  // Свёрнутый сайдбar (иконки-только) — навигация есть на главной-лаунчере.
  // По умолчанию свёрнут; выбор запоминаем.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const s = localStorage.getItem("admin_sidebar_collapsed");
      return s === null ? true : s === "1";
    } catch {
      return true;
    }
  });
  const toggleCollapsed = () =>
    setCollapsed((c) => {
      const n = !c;
      try { localStorage.setItem("admin_sidebar_collapsed", n ? "1" : "0"); } catch { /* ignore */ }
      return n;
    });

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="relative flex w-full h-[100dvh] overflow-hidden bg-bg">
      {/* Sidebar */}
      <aside
        className={clsx(
          "hidden flex-shrink-0 flex-col border-r border-[var(--border)] bg-bg px-2 py-5 md:flex sticky top-0 h-screen overflow-hidden transition-[width] duration-200",
          collapsed ? "w-16" : "w-52",
        )}
      >
        <div className={clsx("mb-6 flex items-center", collapsed ? "flex-col gap-3" : "justify-between px-2.5")}>
          <NavLink
            to="/admin"
            end
            title="Главная"
            className="flex cursor-pointer select-none items-center gap-2 rounded-lg px-1.5 py-1 -mx-1.5 transition-colors hover:bg-bg-subtle"
          >
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-danger/10 text-danger">
              <span className="text-[11px] font-bold tracking-tight">A</span>
            </div>
            {!collapsed && <span className="text-sm font-semibold tracking-tight text-fg">Админ</span>}
          </NavLink>
          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Развернуть меню" : "Свернуть меню"}
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" strokeWidth={1.75} /> : <PanelLeftClose className="h-4 w-4" strokeWidth={1.75} />}
          </button>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
          <GroupedNav itemPad="py-2" canSection={canSection} collapsed={collapsed} />
        </nav>

        <div className="mt-4 flex flex-col gap-0.5 border-t border-[var(--border)] pt-4">
          <NavLink
            to="/"
            title={collapsed ? "Кабинет" : undefined}
            className={clsx(
              "flex items-center rounded-lg py-2 text-sm font-normal text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg",
              collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
            )}
          >
            <ChevronLeft className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            {!collapsed && "Кабинет"}
          </NavLink>
          <div className={clsx("mt-2 flex items-center", collapsed ? "justify-center" : "justify-between px-2.5")}>
            {!collapsed && (
              <span className="truncate text-xs text-fg-subtle">
                {user?.username ? `@${user.username}` : user?.email || user?.name}
              </span>
            )}
            <ThemeSwitcher vertical={collapsed} />
          </div>
          <button
            onClick={handleLogout}
            title={collapsed ? "Выйти" : undefined}
            className={clsx(
              "mt-1 flex items-center rounded-lg py-2 text-sm font-normal text-fg-muted transition-colors hover:bg-danger/8 hover:text-danger",
              collapsed ? "justify-center px-0" : "gap-2.5 px-2.5",
            )}
          >
            <LogOut className="h-4 w-4 flex-shrink-0" strokeWidth={1.75} />
            {!collapsed && "Выйти"}
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-[var(--border)] bg-bg/90 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] backdrop-blur-md md:hidden">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label="Меню админки"
            className="-ml-1 flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <Menu className="h-5 w-5" strokeWidth={1.75} />
          </button>
          <NavLink to="/admin" end className="flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-md bg-danger/10 text-danger">
              <span className="text-[11px] font-bold">A</span>
            </div>
            <span className="text-sm font-semibold tracking-tight text-fg">Админ</span>
          </NavLink>
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
          <aside className="absolute left-0 top-0 flex h-full w-72 max-w-[82%] flex-col overflow-y-auto border-r border-[var(--border)] bg-bg px-2 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))]">
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

            <nav ref={drawerNavRef} className="flex flex-1 flex-col gap-0.5">
              <GroupedNav onNavigate={() => setMenuOpen(false)} itemPad="py-2.5" canSection={canSection} />
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

      {/* Main — единственный скролл-контейнер страницы (app-scroll) */}
      <main className="app-scroll flex-1 min-w-0 px-5 pb-8 pt-[calc(5rem+env(safe-area-inset-top))] md:px-8 md:pt-8">
        <div key={location.pathname} className="mx-auto max-w-6xl animate-fade-in">
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
    </div>
  );
}
