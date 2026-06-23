import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card } from "@/components/ui/Card";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { TelegramLoginButton } from "@/components/TelegramLoginButton";
import { ApiError, type TelegramAuthRequest } from "@/types/api";
import { getTelegramWebApp } from "@/hooks/useTelegramWebApp";

const TELEGRAM_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "";

function getTelegramInitData(): string | null {
  return getTelegramWebApp()?.initData ?? null;
}

export default function LoginPage() {
  const { login, loginWithTelegram, loginWithTelegramWebApp } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const miniAppAttempted = useRef(false);

  // При открытии в Mini App — сразу авторизуемся через initData.
  useEffect(() => {
    if (miniAppAttempted.current) return;
    const initData = getTelegramInitData();
    if (!initData) return;

    miniAppAttempted.current = true;
    window.Telegram?.WebApp?.ready();
    window.Telegram?.WebApp?.expand();

    setIsLoading(true);
    loginWithTelegramWebApp({ init_data: initData })
      .then(() => navigate("/"))
      .catch((err) => {
        setError(
          err instanceof ApiError
            ? err.detail
            : "Не удалось войти через Telegram.",
        );
      })
      .finally(() => setIsLoading(false));
  }, [loginWithTelegramWebApp, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await login({ email, password });
      navigate("/");
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
      navigate("/");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Не удалось войти через Telegram.",
      );
    }
  };

  // Если мы в Mini App — показываем экран загрузки пока идёт вход.
  const isMiniApp = Boolean(getTelegramInitData());
  if (isMiniApp) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg px-4">
      {/* Ambient background glow — single subtle accent */}
      <div aria-hidden className="ambient-glow -top-32 left-1/2 h-96 w-96 -translate-x-1/2" />

      <div className="absolute right-4 top-4 z-10">
        <ThemeSwitcher />
      </div>

      <div className="relative z-10 w-full max-w-[360px] animate-fade-in">
        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="brand-mark mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl text-white">
            <ShieldCheck className="h-6 w-6" strokeWidth={2.2} />
          </div>
          <div className="flex items-baseline justify-center gap-1.5">
            <span className="brand-wordmark text-2xl font-bold tracking-tight">Begemot</span>
            <span className="text-xs font-semibold uppercase tracking-[0.2em] text-fg-subtle">
              VPN
            </span>
          </div>
          <p className="mt-2 text-sm text-fg-muted">
            Управляйте подпиской и устройствами
          </p>
        </div>

        {/* Card */}
        <div className="rounded-xl border border-[var(--border)] bg-bg-raised p-6">
          {TELEGRAM_BOT_USERNAME && (
            <>
              <TelegramLoginButton
                botUsername={TELEGRAM_BOT_USERNAME}
                onAuth={handleTelegramAuth}
              />
              <div className="my-5 flex items-center gap-3">
                <div className="h-px flex-1 bg-[var(--border)]" />
                <span className="text-xs text-fg-subtle">или</span>
                <div className="h-px flex-1 bg-[var(--border)]" />
              </div>
            </>
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
