import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card } from "@/components/ui/Card";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { TelegramLoginButton } from "@/components/TelegramLoginButton";
import { ApiError, type TelegramAuthRequest } from "@/types/api";
import { getTelegramWebApp, whenTelegramReady } from "@/hooks/useTelegramWebApp";
import { BrandWordmark } from "@/components/BrandWordmark";
import { BrandLogo } from "@/components/BrandLogo";
import { useBranding } from "@/contexts/BrandingContext";

const TELEGRAM_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "";

function getTelegramInitData(): string | null {
  return getTelegramWebApp()?.initData ?? null;
}

export default function LoginPage() {
  const { login, loginWithTelegram, loginWithTelegramWebApp, user } = useAuth();
  const { telegramOidcEnabled } = useBranding();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Куда вернуть после входа (напр. /devices из кнопки «Подключиться» в боте).
  // Только безопасные внутренние пути.
  const rawNext = searchParams.get("next");
  const next = rawNext && rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // ?error=telegram прилетает с OIDC-callback при сбое обмена/проверки токена.
  const [error, setError] = useState<string | null>(
    searchParams.get("error") === "telegram"
      ? "Не удалось войти через Telegram. Попробуйте ещё раз."
      : null,
  );
  const [isLoading, setIsLoading] = useState(false);
  // Mini App ожидается, пока асинхронно грузится Telegram SDK (см. index.html).
  // Держим экран загрузки до окончания загрузки, чтобы не мелькала форма входа.
  const [miniAppPending, setMiniAppPending] = useState(
    () => Boolean(window.__tgWebAppExpected) && !window.__tgWebAppSettled,
  );
  const miniAppAttempted = useRef(false);

  // При открытии в Mini App — сразу авторизуемся через initData.
  // Ждём загрузки SDK, т.к. telegram-web-app.js грузится асинхронно.
  useEffect(() => {
    if (miniAppAttempted.current) return;
    let cancelled = false;

    whenTelegramReady().then(() => {
      if (cancelled) return;
      setMiniAppPending(false);

      const initData = getTelegramInitData();
      if (!initData || miniAppAttempted.current) return;

      miniAppAttempted.current = true;
      window.Telegram?.WebApp?.ready();
      window.Telegram?.WebApp?.expand();

      setIsLoading(true);
      loginWithTelegramWebApp({ init_data: initData })
        .then(() => navigate(next))
        .catch((err) => {
          setError(
            err instanceof ApiError
              ? err.detail
              : "Не удалось войти через Telegram.",
          );
        })
        .finally(() => setIsLoading(false));
    });

    return () => {
      cancelled = true;
    };
  }, [loginWithTelegramWebApp, navigate, next]);

  // Уже залогинен (сессия жива — access 15 мин, refresh 30 дней) — не показываем
  // форму входа и не гоняем через Telegram повторно, сразу уводим в кабинет.
  useEffect(() => {
    if (user) navigate(next, { replace: true });
  }, [user, navigate, next]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await login({ email, password });
      navigate(next);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Не удалось войти. Попробуйте снова.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleTelegramAuth = async (data: TelegramAuthRequest) => {
    setError(null);
    try {
      await loginWithTelegram(data);
      navigate(next);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Не удалось войти через Telegram.",
      );
    }
  };

  // Если мы в Mini App (или ещё ждём загрузки его SDK) — показываем экран загрузки.
  const isMiniApp = miniAppPending || Boolean(getTelegramInitData());
  if (isMiniApp) {
    return (
      <div className="app-scroll flex min-h-screen items-center justify-center bg-bg">
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-accent text-accent-fg shadow-glow">
            <span className="text-lg font-bold">R</span>
          </div>
          {error ? (
            <p className="text-sm text-danger">{error}</p>
          ) : (
            <p className="text-sm text-fg-subtle">Выполняется вход...</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app-scroll relative flex min-h-screen items-center justify-center overflow-x-hidden bg-bg px-4">
      {/* Ambient background glow — single subtle accent */}
      <div aria-hidden className="ambient-glow -top-32 left-1/2 h-96 w-96 -translate-x-1/2" />

      <div className="absolute right-4 top-4 z-10">
        <ThemeSwitcher />
      </div>

      <div className="relative z-10 w-full max-w-[360px] animate-fade-in">
        {/* Brand */}
        <div className="mb-8 text-center">
          <BrandLogo size={48} className="mx-auto mb-4" />
          <div className="flex items-baseline justify-center">
            <BrandWordmark className="text-2xl" />
          </div>
          <p className="mt-2 text-sm text-fg-muted">
            Управляйте подпиской и устройствами
          </p>
        </div>

        {/* Card */}
        <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-6">
          {(telegramOidcEnabled || TELEGRAM_BOT_USERNAME) && (
            <>
              {/* Если включён OIDC — показываем ТОЛЬКО его кнопку. Классический
                  Login Widget оставляем лишь как запасной, когда OIDC выключен
                  (иначе он рендерит «Bot domain invalid», т.к. требует /setdomain). */}
              <div className="flex flex-col items-stretch gap-2.5">
                {telegramOidcEnabled && (
                  // Новый флоу Telegram (OpenID Connect): редирект на oauth.telegram.org.
                  <button
                    type="button"
                    onClick={() => {
                      window.location.href = "/api/auth/telegram/oidc/start";
                    }}
                    className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#2aabee] text-sm font-medium text-white transition-colors hover:bg-[#1f97d4]"
                  >
                    <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current" aria-hidden>
                      <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />
                    </svg>
                    Войти через Telegram
                  </button>
                )}
                {!telegramOidcEnabled && TELEGRAM_BOT_USERNAME && (
                  <div className="flex justify-center">
                    <TelegramLoginButton
                      botUsername={TELEGRAM_BOT_USERNAME}
                      onAuth={handleTelegramAuth}
                    />
                  </div>
                )}
              </div>
              <div className="my-5 flex items-center gap-3">
                <div className="h-px flex-1 bg-[var(--border)]" />
                <span className="text-xs text-fg-subtle">или</span>
                <div className="h-px flex-1 bg-[var(--border)]" />
              </div>
            </>
          )}

          {searchParams.get("reset") === "ok" && (
            <p className="mb-4 rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
              Пароль изменён. Войдите с новым паролем.
            </p>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <Input
              label="Email"
              type="email"
              name="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Input
              label="Пароль"
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <div className="-mt-1 text-right">
              <Link
                to="/reset-password"
                className="text-xs text-fg-subtle transition-colors hover:text-accent"
              >
                Забыли пароль?
              </Link>
            </div>

            {error && <p className="text-xs text-danger">{error}</p>}

            <Button type="submit" isLoading={isLoading} className="mt-1 h-9 w-full text-sm">
              Войти
            </Button>
          </form>
        </div>

        <p className="mt-4 text-center text-sm text-fg-subtle">
          Нет аккаунта?{" "}
          <Link to="/register" className="font-medium text-fg hover:text-accent transition-colors">
            Зарегистрироваться
          </Link>
        </p>
      </div>
    </div>
  );
}
