import { Link } from "react-router-dom";
import { Globe2, Gauge, Infinity as InfinityIcon, Landmark } from "lucide-react";
import { BrandLogo } from "@/components/BrandLogo";
import { BrandWordmark } from "@/components/BrandWordmark";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useT } from "@/i18n/I18nContext";

/**
 * Публичная начальная страница (лендинг) для неавторизованных.
 * Кратко объясняет сервис и ведёт на регистрацию/вход.
 * ВАЖНО: в текстах НЕ используем слово «VPN» — только нейтральные формулировки
 * (безопасный доступ в интернет, доступ к зарубежным ресурсам).
 */
export default function LandingPage() {
  const t = useT();

  const features = [
    { icon: Gauge, title: t("landing.f1.title"), text: t("landing.f1.text") },
    { icon: InfinityIcon, title: t("landing.f2.title"), text: t("landing.f2.text") },
    { icon: Landmark, title: t("landing.f3.title"), text: t("landing.f3.text") },
    { icon: Globe2, title: t("landing.f4.title"), text: t("landing.f4.text") },
  ];

  return (
    <div className="app-scroll h-full bg-bg">
      <div className="relative flex min-h-full flex-col overflow-x-hidden px-4">
      {/* Мягкое фоновое свечение акцентом */}
      <div aria-hidden className="ambient-glow -top-40 left-1/2 h-[28rem] w-[28rem] -translate-x-1/2" />

      {/* Шапка: бренд + переключатели. z-30 — выше <main> (z-10), иначе выпадашка
          языка перекрывается героем: меню видно, но клики уходят в текст. */}
      <header className="relative z-30 mx-auto flex w-full max-w-5xl items-center justify-between py-5">
        <div className="flex items-center gap-2.5">
          <BrandLogo size={32} />
          <BrandWordmark className="text-lg" showSuffix={false} />
        </div>
        <div className="flex items-center gap-2">
          <LanguageSwitcher />
          <ThemeSwitcher />
        </div>
      </header>

      {/* Герой */}
      <main className="relative z-10 mx-auto flex w-full max-w-5xl flex-1 flex-col items-center justify-center py-10 text-center">
        <h1 className="max-w-2xl text-balance text-3xl font-bold leading-tight tracking-tight text-fg sm:text-4xl md:text-5xl animate-fade-in">
          {t("landing.title")}
        </h1>
        <p className="mt-4 max-w-xl text-pretty text-base text-fg-muted sm:text-lg animate-fade-in">
          {t("landing.subtitle")}
        </p>

        {/* CTA */}
        <div className="mt-8 flex w-full max-w-sm flex-col items-stretch gap-3 sm:w-auto sm:flex-row sm:justify-center animate-fade-in">
          <Link
            to="/register"
            className="inline-flex h-11 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] px-6 text-sm font-semibold text-white shadow-[0_8px_20px_-8px_var(--accent-glow)] transition-all duration-150 hover:brightness-[1.06] hover:shadow-[0_12px_28px_-6px_var(--accent-glow)] active:scale-[0.98]"
          >
            {t("landing.getStarted")}
          </Link>
          <Link
            to="/login"
            className="inline-flex h-11 items-center justify-center rounded-lg border border-[var(--border)] bg-bg-raised px-6 text-sm font-semibold text-fg transition-all duration-150 hover:border-[var(--accent)] hover:bg-bg-overlay active:scale-[0.98]"
          >
            {t("landing.signIn")}
          </Link>
        </div>

        {/* Преимущества */}
        <div className="mt-14 grid w-full grid-cols-1 gap-3 text-left sm:grid-cols-2 lg:grid-cols-4">
          {features.map(({ icon: Icon, title, text }) => (
            <div
              key={title}
              className="rounded-xl border border-border-subtle bg-bg-raised p-5 transition-colors hover:border-[var(--border)]"
            >
              <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-subtle bg-gradient-to-br from-accent-subtle to-transparent text-accent ring-1 ring-inset ring-[color:var(--accent-subtle)] shadow-[0_8px_20px_-10px_var(--accent-glow)]">
                <Icon className="h-6 w-6" strokeWidth={1.75} />
              </div>
              <h3 className="text-sm font-semibold text-fg">{title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-fg-muted">{text}</p>
            </div>
          ))}
        </div>
      </main>

      {/* Подвал: статус + тарифы */}
      <footer className="relative z-10 mx-auto flex w-full max-w-5xl flex-wrap items-center justify-center gap-x-6 gap-y-2 py-6 text-sm">
        <Link
          to="/status"
          className="inline-flex items-center gap-2 text-fg-muted transition-colors hover:text-accent"
        >
          <span className="h-2 w-2 rounded-full bg-success" />
          {t("status.title")}
        </Link>
        <Link to="/pricing" className="text-fg-muted transition-colors hover:text-accent">
          {t("landing.viewPlans")}
        </Link>
      </footer>
      </div>
    </div>
  );
}
