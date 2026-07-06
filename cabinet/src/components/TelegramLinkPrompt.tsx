import { useState } from "react";
import { Link } from "react-router-dom";
import { Send, X, ArrowRight } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useT } from "@/i18n/I18nContext";

const DISMISS_KEY = "tg-link-prompt-dismissed";

/**
 * Ненавязчивое предложение привязать Telegram для пользователей,
 * зарегистрированных по email. Необязательно: баннер можно закрыть,
 * выбор запоминается в localStorage. Сама привязка — в настройках
 * (TelegramLinkBlock на странице /settings).
 */
export function TelegramLinkPrompt() {
  const t = useT();
  const { user } = useAuth();
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1",
  );

  // Показываем только email-пользователям без привязанного Telegram.
  if (
    !user ||
    user.telegram_id ||
    user.auth_type?.toUpperCase() !== "EMAIL" ||
    dismissed
  ) {
    return null;
  }

  const handleDismiss = () => {
    localStorage.setItem(DISMISS_KEY, "1");
    setDismissed(true);
  };

  return (
    <div className="relative flex items-start gap-3 rounded-2xl border border-accent/25 bg-accent-subtle/60 p-4 sm:p-5">
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_8px_20px_-8px_var(--accent-glow)]">
        <Send className="h-5 w-5" />
      </div>
      <div className="min-w-0 flex-1 pr-6">
        <p className="text-sm font-semibold text-fg">{t("tgPrompt.title")}</p>
        <p className="mt-0.5 text-sm text-fg-muted">
          {t("tgPrompt.text")}
        </p>
        <Link
          to="/settings"
          className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-accent transition-opacity hover:opacity-80"
        >
          {t("tgPrompt.link")}
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
      <button
        onClick={handleDismiss}
        aria-label={t("common.hide")}
        className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-bg-overlay hover:text-fg"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
