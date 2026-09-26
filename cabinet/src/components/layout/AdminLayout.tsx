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
  DoorOpen,
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
  Megaphone,
  Percent,
  Undo2,
  BadgePercent,
  BarChart3,
  Umbrella,
  Gauge,
  Snowflake,
  Database,
  ShieldCheck,
  MonitorSmartphone,
  BellRing,
  HeartPulse,} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

/** Языки админки: владелец 20.09 — «админка на русском и англ, больше не надо». */
const ADMIN_LANGS = ["ru", "en"] as const;
import { useT } from "@/i18n/I18nContext";
import { Admin2FAUnlock } from "@/components/admin/Admin2FA";
import { AdminNotifBell } from "@/components/admin/AdminNotifBell";
import { updatesAdminApi } from "@/api/admin";

// section — ключ раздела прав (см. backend permissions.py). Пункт показывается,
// только если у пользователя есть доступ к разделу (fullAccess или в списке).
export type NavItem = { to: string; icon: LucideIcon; label: string; end?: boolean; section: string };

// Разделы сгруппированы по категориям (заголовки в сайдбаре), чтобы длинный
// список не висел плоской простынёй. Экспортируется — тот же список рисуется
// плиточным лаунчером на главной админки (Обзор).
export const navGroups: { title: string; items: NavItem[] }[] = [
  {
    title: "adm.navgrp.analytics",
    items: [
      { to: "/admin/stats", icon: LayoutDashboard, label: "adm.nav.stats", section: "dashboard" },
      { to: "/admin/transactions", icon: CreditCard, label: "adm.nav.transactions", section: "transactions" },
    ],
  },
  {
    title: "adm.navgrp.users",
    items: [
      { to: "/admin/users", icon: Users, label: "adm.nav.users", section: "users" },
      { to: "/admin/referral", icon: Gift, label: "adm.nav.referral", section: "settings" },
      { to: "/admin/import", icon: DownloadCloud, label: "adm.nav.import", section: "import" },
      { to: "/admin/abuse", icon: Fingerprint, label: "adm.nav.abuse", section: "abuse" },
      { to: "/admin/support", icon: LifeBuoy, label: "adm.nav.support", section: "support" },
      { to: "/admin/churn-signals", icon: HeartPulse, label: "adm.nav.churn_signals", section: "settings" },
    ],
  },
  {
    title: "adm.navgrp.sales",
    items: [
      { to: "/admin/plans", icon: Package, label: "adm.nav.plans", section: "plans" },
      { to: "/admin/promocodes", icon: Tag, label: "adm.nav.promocodes", section: "promocodes" },
      { to: "/admin/gateways", icon: Wallet, label: "adm.nav.gateways", section: "gateways" },
      { to: "/admin/topup", icon: Coins, label: "adm.nav.topup", section: "settings" },
      { to: "/admin/extra-device", icon: MonitorSmartphone, label: "adm.nav.extra_device", section: "settings" },
      { to: "/admin/extra-traffic", icon: Gauge, label: "adm.nav.extra_traffic", section: "settings" },
      { to: "/admin/payment-reminder", icon: BellRing, label: "adm.nav.payment_reminder", section: "settings" },
      { to: "/admin/reserve", icon: Umbrella, label: "adm.nav.reserve", section: "settings" },
      { to: "/admin/freeze", icon: Snowflake, label: "adm.nav.freeze", section: "settings" },
    ],
  },
  {
    title: "adm.navgrp.marketing",
    items: [
      { to: "/admin/ad-links", icon: Link2, label: "adm.nav.ad_links", section: "ad_links" },
      { to: "/admin/broadcasts", icon: Radio, label: "adm.nav.broadcasts", section: "broadcasts" },
      { to: "/admin/promo-banner", icon: Megaphone, label: "adm.nav.promo_banner", section: "settings" },
      { to: "/admin/trial-discount", icon: Percent, label: "adm.nav.trial_discount", section: "settings" },
      { to: "/admin/winback", icon: Undo2, label: "adm.nav.winback", section: "settings" },
      { to: "/admin/renewal-discount", icon: BadgePercent, label: "adm.nav.renewal_discount", section: "settings" },
      { to: "/admin/digest", icon: BarChart3, label: "adm.nav.digest", section: "settings" },
      { to: "/admin/traffic-alert", icon: Gauge, label: "adm.nav.traffic_alert", section: "settings" },
      { to: "/admin/new-device", icon: Smartphone, label: "adm.nav.new_device", section: "settings" },
    ],
  },
  {
    title: "adm.navgrp.cabinet",
    items: [
      { to: "/admin/appearance", icon: Palette, label: "adm.nav.appearance", section: "content" },
      { to: "/admin/cabinet", icon: DoorOpen, label: "adm.nav.cabinet", section: "content" },
      { to: "/admin/info", icon: Info, label: "adm.nav.info", section: "content" },
      { to: "/admin/menu", icon: SquareMenu, label: "adm.nav.menu", section: "content" },
      { to: "/admin/apps", icon: Smartphone, label: "adm.nav.apps", section: "content" },
      {
        to: "/admin/subscription-app",
        icon: RouteIcon,
        label: "adm.nav.subscription_app",
        section: "settings",
      },
      { to: "/admin/server-status", icon: Activity, label: "adm.nav.server_status", section: "settings" },
      { to: "/admin/email", icon: Mail, label: "adm.nav.email", section: "settings" },
    ],
  },
  {
    title: "adm.navgrp.system",
    items: [
      { to: "/admin/remnawave", icon: Waves, label: "adm.nav.remnawave", section: "remnawave" },
      { to: "/admin/auth", icon: KeyRound, label: "adm.nav.auth", section: "settings" },
      { to: "/admin/settings", icon: Settings, label: "adm.nav.settings", section: "settings" },
      { to: "/admin/notifications", icon: Bell, label: "adm.nav.notifications", section: "settings" },
      { to: "/admin/summary", icon: Sunrise, label: "adm.nav.summary", section: "settings" },
      { to: "/admin/audit", icon: ShieldAlert, label: "adm.nav.audit", section: "audit" },
      { to: "/admin/backup", icon: Database, label: "adm.nav.backup", section: "settings" },
      { to: "/admin/admin-ip", icon: ShieldCheck, label: "adm.nav.admin_ip", section: "settings" },
      { to: "/admin/updates", icon: Sparkles, label: "adm.nav.updates", section: "updates" },
    ],
  },
];

function GroupedNav({
  onNavigate,
  itemPad,
  canSection,
  canPage,
  collapsed = false,
}: {
  onNavigate?: () => void;
  itemPad: string;
  canSection: (key: string) => boolean;
  canPage: (path: string) => boolean;
  collapsed?: boolean;
}) {
  const t = useT();
  const groups = navGroups
    // Два разных фильтра: canSection — про ПРАВА администратора, canPage — про
    // УМЕНИЕ бэкенда. Раздел «Настройки» у нас один на два десятка страниц, и без
    // второго фильтра отсутствие одной ручки прячет весь блок целиком (ровно это
    // и делало админку поверх чужого бота «скудной»).
    .map((group) => ({
      ...group,
      items: group.items.filter((it) => canSection(it.section) && canPage(it.to)),
    }))
    .filter((group) => group.items.length > 0);
  return (
    <>
      {groups.map((group) => (
        <div key={group.title} className="flex flex-col gap-0.5">
          {collapsed ? (
            <span className="mx-auto my-1.5 h-px w-6 bg-[var(--border)]" />
          ) : (
            <span className="px-2.5 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">
              {t(group.title)}
            </span>
          )}
          {group.items.map(({ to, icon: Icon, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onNavigate}
              title={collapsed ? t(label) : undefined}
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
              {!collapsed && t(label)}
            </NavLink>
          ))}
        </div>
      ))}
    </>
  );
}

export function AdminLayout({ children }: { children: ReactNode }) {
  const { user, logout, isReadonlyAdmin, canSection, canPage } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  // Установленная версия — чтобы при разборе «а что у тебя стоит» не лазить в
  // консоль сервера. Ручка лёгкая (читает файл VERSION), без похода на GitHub.
  // Не ответила — бейджа просто нет: на чужом бэкенде такой ручки может не быть.
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    updatesAdminApi
      .version()
      .then((r) => setVersion(r.version))
      .catch(() => setVersion(null));
  }, []);
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
      <Admin2FAUnlock />
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
          <div className={clsx("flex items-center", collapsed ? "flex-col gap-1" : "gap-0.5")}>
            <AdminNotifBell />
            <button
              onClick={toggleCollapsed}
              title={collapsed ? "Развернуть меню" : "Свернуть меню"}
              aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-bg-subtle hover:text-fg"
            >
              {collapsed ? <PanelLeftOpen className="h-4 w-4" strokeWidth={1.75} /> : <PanelLeftClose className="h-4 w-4" strokeWidth={1.75} />}
            </button>
          </div>
        </div>

        {/* scrollbar-thin: без него разделов больше, чем влезает, и сбоку
            появляется толстая системная полоса прокрутки поверх меню. */}
        <nav className="scrollbar-thin flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
          <GroupedNav itemPad="py-2" canSection={canSection} canPage={canPage} collapsed={collapsed} />
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
          {!collapsed && (
            <div className="mt-2 px-2.5">
              <span className="truncate text-xs text-fg-subtle">
                {user?.username ? `@${user.username}` : user?.email || user?.name}
              </span>
            </div>
          )}
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
        {/* min-w-0 обязателен: без него левый блок не сжимается, и правый
            (колокольчик, версия, темы) выталкивается за правый край — на 320 px
            это 26 px наружу, и страница начинает ездить вбок пальцем. */}
        <div className="flex min-w-0 items-center gap-2">
          <button
            onClick={() => setMenuOpen(true)}
            aria-label="Меню админки"
            className="-ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <Menu className="h-5 w-5" strokeWidth={1.75} />
          </button>
          <NavLink to="/admin" end className="flex min-w-0 items-center gap-2">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-danger/10 text-danger">
              <span className="text-[11px] font-bold">A</span>
            </div>
            <span className="truncate text-sm font-semibold tracking-tight text-fg">Админ</span>
          </NavLink>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <AdminNotifBell />
          {/* Всегда на виду — выход в кабинет без скролла вниз по меню. */}
          <NavLink
            to="/"
            title="В кабинет"
            className="flex h-8 items-center gap-1 rounded-lg px-2 text-fg-muted transition-colors hover:bg-bg-subtle hover:text-fg"
          >
            <ChevronLeft className="h-4 w-4" strokeWidth={1.75} />
            {/* На узком экране остаётся одна стрелка: подпись вместе с версией и
                переключателем тем не помещалась в строку и выталкивала их за
                правый край (замер на 390 px: правый край 407). */}
            <span className="hidden text-sm font-medium sm:inline">Кабинет</span>
          </NavLink>
          {version && (
            <NavLink
              to="/admin/updates"
              title="Версия бэкенда (бота)"
              className="hidden font-mono text-[10px] text-fg-subtle transition-colors hover:text-accent min-[360px]:inline"
            >
              v{version}
            </NavLink>
          )}
          <LanguageSwitcher only={ADMIN_LANGS} />
          <ThemeSwitcher />
        </div>
      </div>

      {/* Mobile menu drawer — все разделы (не помещаются в нижний навбар) */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div
            className="absolute inset-0 bg-black/40 animate-fade-in"
            onClick={() => setMenuOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-72 max-w-[82%] flex-col overflow-hidden border-r border-[var(--border)] bg-bg px-2 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))]">
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

            <nav ref={drawerNavRef} className="scrollbar-thin flex flex-1 flex-col gap-0.5 overflow-y-auto min-h-0">
              <GroupedNav onNavigate={() => setMenuOpen(false)} itemPad="py-2.5" canSection={canSection} canPage={canPage} />
            </nav>

            <div className="mt-4 flex flex-shrink-0 flex-col gap-0.5 border-t border-[var(--border)] pt-4">
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
        {/* Переключатель темы — вверху справа области контента (в потоке: прокручивается со страницей, не парит). Десктоп; на мобиле он в верхней панели. */}
        <div className="mb-3 hidden items-center justify-end gap-2 md:flex">
          {version && (
            <NavLink
              to="/admin/updates"
              title="Версия бэкенда (бота) — открыть «Обновления»"
              className="rounded-lg border border-[var(--border)] px-2 py-1 font-mono text-[11px] text-fg-subtle transition-colors hover:border-accent hover:text-accent"
            >
              v{version}
            </NavLink>
          )}
          <LanguageSwitcher only={ADMIN_LANGS} />
          <ThemeSwitcher />
        </div>
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
