import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, AlertCircle, LogOut } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { authApi } from "@/api/auth";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { TelegramLoginButton } from "@/components/TelegramLoginButton";
import { ApiError } from "@/types/api";
import type { TelegramAuthRequest } from "@/types/api";

const TELEGRAM_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "";

function EmailVerificationBlock() {
  const { user, refreshMe } = useAuth();
  const [code, setCode] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );
  const [codeSent, setCodeSent] = useState(false);

  // TG-пользователи без email не должны видеть этот блок — у них нет почты для верификации.
  if (!user || user.is_email_verified || user.auth_type?.toUpperCase() === "TELEGRAM") return null;

  const handleSendCode = async () => {
    setIsSending(true);
    setMessage(null);
    try {
      await authApi.requestEmailVerification();
      setCodeSent(true);
      setMessage({ type: "success", text: "Код отправлен на почту" });
    } catch (e) {
      setMessage({
        type: "error",
        text: e instanceof ApiError ? e.detail : "Не удалось отправить код",
      });
    } finally {
      setIsSending(false);
    }
  };

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsConfirming(true);
    setMessage(null);
    try {
      await authApi.confirmEmailVerification({ code });
      await refreshMe();
      setMessage({ type: "success", text: "Email подтверждён!" });
    } catch (e) {
      setMessage({
        type: "error",
        text: e instanceof ApiError ? e.detail : "Неверный код",
      });
    } finally {
      setIsConfirming(false);
    }
  };

  return (
    <Card variant="bordered">
      <CardHeader
        title="Email не подтверждён"
        subtitle="Подтвердите почту, чтобы покупать и продлевать подписку"
      />
      {!codeSent ? (
        <Button size="sm" variant="secondary" onClick={handleSendCode} isLoading={isSending}>
          Отправить код подтверждения
        </Button>
      ) : (
        <form onSubmit={handleConfirm} className="flex gap-2">
          <Input
            name="code"
            placeholder="123456"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className="flex-1"
          />
          <Button type="submit" size="sm" isLoading={isConfirming}>
            Подтвердить
          </Button>
        </form>
      )}
      {message && (
        <p
          className={`mt-2 text-sm ${
            message.type === "success" ? "text-success" : "text-danger"
          }`}
        >
          {message.text}
        </p>
      )}
    </Card>
  );
}

function BackupAccessBlock() {
  const { user, hasPassword, refreshMe } = useAuth();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Только для Telegram-пользователей — резервный вход по email.
  if (!user || user.auth_type?.toUpperCase() !== "TELEGRAM") return null;

  const emailVerified = user.is_email_verified && !!user.email;
  const fullyConfigured = emailVerified && hasPassword;

  const notify = (e: unknown, fallback: string) =>
    setMessage({ type: "error", text: e instanceof ApiError ? e.detail : fallback });

  const handleSendCode = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await authApi.requestEmailVerification(email);
      setCodeSent(true);
      setMessage({ type: "success", text: "Код отправлен на указанную почту" });
    } catch (e) {
      notify(e, "Не удалось отправить код");
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await authApi.confirmEmailVerification({ code });
      await refreshMe();
      setCode("");
      setCodeSent(false);
      setMessage({ type: "success", text: "Email подтверждён" });
    } catch (e) {
      notify(e, "Неверный код");
    } finally {
      setBusy(false);
    }
  };

  const handleSetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await authApi.setPassword(password);
      await refreshMe();
      setPassword("");
      setMessage({ type: "success", text: "Пароль установлен" });
    } catch (e) {
      notify(e, "Не удалось установить пароль");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card variant="bordered">
      <CardHeader
        title="Резервный доступ по email"
        subtitle="Чтобы не потерять аккаунт, если Telegram заблокируют — добавьте почту и пароль для входа"
      />

      {fullyConfigured ? (
        <div className="flex items-start gap-2 rounded-xl border border-success/30 bg-success/8 px-4 py-3">
          <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
          <div className="text-sm">
            <p className="font-medium text-fg">Резервный доступ настроен</p>
            <p className="text-fg-muted">
              Вход по email <span className="text-fg">{user.email}</span> и паролю доступен.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {/* Шаг 1 — email */}
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${
                  emailVerified ? "bg-success text-white" : "bg-accent text-accent-fg"
                }`}
              >
                {emailVerified ? "✓" : "1"}
              </span>
              <span className="text-sm font-medium text-fg">Подтвердите email</span>
            </div>
            {emailVerified ? (
              <p className="pl-7 text-sm text-fg-muted">{user.email}</p>
            ) : !codeSent ? (
              <div className="flex gap-2 pl-7">
                <Input
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="flex-1"
                />
                <Button size="sm" variant="secondary" onClick={handleSendCode} isLoading={busy} disabled={!email}>
                  Отправить код
                </Button>
              </div>
            ) : (
              <form onSubmit={handleConfirm} className="flex gap-2 pl-7">
                <Input
                  placeholder="123456"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  className="flex-1"
                />
                <Button type="submit" size="sm" isLoading={busy}>
                  Подтвердить
                </Button>
              </form>
            )}
          </div>

          {/* Шаг 2 — пароль */}
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${
                  hasPassword ? "bg-success text-white" : emailVerified ? "bg-accent text-accent-fg" : "bg-bg-overlay text-fg-subtle"
                }`}
              >
                {hasPassword ? "✓" : "2"}
              </span>
              <span className={`text-sm font-medium ${emailVerified ? "text-fg" : "text-fg-subtle"}`}>
                Задайте пароль
              </span>
            </div>
            {hasPassword ? (
              <p className="pl-7 text-sm text-fg-muted">Пароль установлен</p>
            ) : emailVerified ? (
              <form onSubmit={handleSetPassword} className="flex gap-2 pl-7">
                <Input
                  type="password"
                  placeholder="Минимум 8 символов"
                  autoComplete="new-password"
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="flex-1"
                />
                <Button type="submit" size="sm" isLoading={busy} disabled={password.length < 8}>
                  Сохранить
                </Button>
              </form>
            ) : (
              <p className="pl-7 text-sm text-fg-subtle">Сначала подтвердите email</p>
            )}
          </div>
        </div>
      )}

      {message && (
        <p className={`mt-3 text-sm ${message.type === "success" ? "text-success" : "text-danger"}`}>
          {message.text}
        </p>
      )}
    </Card>
  );
}

// Управление уже привязанной и подтверждённой почтой: сменить или удалить.
function ManageEmailBlock() {
  const { user, refreshMe } = useAuth();
  const [mode, setMode] = useState<"idle" | "change">("idle");
  const [newEmail, setNewEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Показываем только когда почта есть и подтверждена.
  if (!user || !user.email || !user.is_email_verified) return null;

  const canDelete = Boolean(user.telegram_id); // есть запасной вход — удалять безопасно
  const notify = (e: unknown, fallback: string) =>
    setMessage({ type: "error", text: e instanceof ApiError ? e.detail : fallback });

  const reset = () => {
    setMode("idle");
    setNewEmail("");
    setCode("");
    setCodeSent(false);
  };

  const handleSendCode = async () => {
    setBusy(true);
    setMessage(null);
    try {
      // Сначала переводим аккаунт в режим смены: changeEmail сбрасывает признак
      // подтверждения и ставит pending_email. Иначе request-verification для уже
      // подтверждённой почты отвечает 409 «доступно только без подтверждённого email».
      await authApi.changeEmail({ email: newEmail });
      await authApi.requestEmailVerification(newEmail);
      setCodeSent(true);
      setMessage({ type: "success", text: "Код отправлен на новый адрес" });
    } catch (e) {
      notify(e, "Не удалось отправить код");
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await authApi.confirmEmailVerification({ code });
      await refreshMe();
      reset();
      setMessage({ type: "success", text: "Email изменён" });
    } catch (e) {
      notify(e, "Неверный код");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Удалить почту ${user.email}? Вход останется через Telegram.`)) return;
    setBusy(true);
    setMessage(null);
    try {
      await authApi.deleteEmail();
      await refreshMe();
      reset();
      setMessage({ type: "success", text: "Email удалён" });
    } catch (e) {
      notify(e, "Не удалось удалить почту");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card variant="bordered">
      <CardHeader title="Почта" subtitle={`Текущая: ${user.email}`} />

      {mode === "idle" ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setMode("change")}>
            Изменить email
          </Button>
          {canDelete && (
            <Button size="sm" variant="danger" onClick={handleDelete} isLoading={busy}>
              Удалить email
            </Button>
          )}
        </div>
      ) : !codeSent ? (
        <div className="flex gap-2">
          <Input
            type="email"
            placeholder="new@example.com"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            className="flex-1"
          />
          <Button size="sm" variant="secondary" onClick={handleSendCode} isLoading={busy} disabled={!newEmail}>
            Отправить код
          </Button>
          <Button size="sm" variant="ghost" onClick={reset}>
            Отмена
          </Button>
        </div>
      ) : (
        <form onSubmit={handleConfirm} className="flex gap-2">
          <Input
            placeholder="123456"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            className="flex-1"
          />
          <Button type="submit" size="sm" isLoading={busy}>
            Подтвердить
          </Button>
          <Button size="sm" variant="ghost" onClick={reset}>
            Отмена
          </Button>
        </form>
      )}

      {!canDelete && mode === "idle" && (
        <p className="mt-2 text-xs text-fg-subtle">
          Удаление недоступно: email — единственный способ входа. Привяжите Telegram, чтобы удалить почту.
        </p>
      )}
      {message && (
        <p className={`mt-2 text-sm ${message.type === "success" ? "text-success" : "text-danger"}`}>
          {message.text}
        </p>
      )}
    </Card>
  );
}

function ChangePasswordBlock() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setMessage(null);
    try {
      await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setMessage({ type: "success", text: "Пароль изменён" });
      setCurrentPassword("");
      setNewPassword("");
    } catch (e) {
      setMessage({
        type: "error",
        text: e instanceof ApiError ? e.detail : "Не удалось изменить пароль",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Card variant="bordered">
      <CardHeader title="Смена пароля" />
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Input
          type="password"
          label="Текущий пароль"
          autoComplete="current-password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
        />
        <Input
          type="password"
          label="Новый пароль"
          autoComplete="new-password"
          minLength={8}
          required
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
        />
        {message && (
          <p
            className={`text-sm ${
              message.type === "success" ? "text-success" : "text-danger"
            }`}
          >
            {message.text}
          </p>
        )}
        <Button type="submit" isLoading={isLoading} className="self-start">
          Сохранить
        </Button>
      </form>
    </Card>
  );
}

// Сообщения по результату OIDC-привязки (?tg=... в URL после возврата с oauth.telegram.org).
const TG_LINK_RESULTS: Record<string, { type: "success" | "error"; text: string }> = {
  linked: { type: "success", text: "Telegram привязан" },
  already: { type: "error", text: "К аккаунту уже привязан другой Telegram" },
  conflict: {
    type: "error",
    text: "У этого Telegram уже есть отдельный аккаунт с подпиской. Объединение пока недоступно — напишите в поддержку.",
  },
  error: { type: "error", text: "Не удалось привязать Telegram" },
};

function TelegramLinkBlock() {
  const { user, refreshMe } = useAuth();
  const { telegramOidcEnabled } = useBranding();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Разбираем результат возврата с OIDC-привязки и чистим URL, чтобы сообщение
  // не «залипало» при перезагрузке.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const tag = params.get("tg");
    if (!tag) return;
    setResult(TG_LINK_RESULTS[tag] ?? null);
    if (tag === "linked") void refreshMe();
    params.delete("tg");
    const qs = params.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (qs ? `?${qs}` : ""),
    );
  }, [refreshMe]);

  if (!user || user.telegram_id) {
    return (
      <Card variant="bordered">
        <CardHeader title="Telegram" />
        <p className="text-sm text-fg-muted">
          {user?.telegram_id
            ? `Аккаунт привязан (ID: ${user.telegram_id})`
            : "Загрузка..."}
        </p>
        {result && (
          <p className={`mt-2 text-sm ${result.type === "success" ? "text-success" : "text-danger"}`}>
            {result.text}
          </p>
        )}
      </Card>
    );
  }

  const handleLink = async (data: TelegramAuthRequest) => {
    setError(null);
    try {
      await authApi.linkTelegram(data);
      await refreshMe();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Не удалось привязать Telegram");
    }
  };

  return (
    <Card variant="bordered">
      <CardHeader title="Привязать Telegram" subtitle="Для быстрого входа и уведомлений" />
      {/* Оба способа, если доступны: новый OIDC (без «deprecated») + классический
          виджет как запасной. Включение OIDC не убирает старую привязку. */}
      <div className="flex flex-col items-start gap-2.5">
        {telegramOidcEnabled && (
          // Новый флоу Telegram (OpenID Connect): привязка к текущему аккаунту.
          <button
            type="button"
            onClick={() => {
              window.location.href = "/api/auth/telegram/oidc/start?mode=link";
            }}
            className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[#2aabee] text-sm font-medium text-white transition-colors hover:bg-[#1f97d4] sm:w-auto sm:px-5"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current" aria-hidden>
              <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />
            </svg>
            Привязать через Telegram
          </button>
        )}
        {TELEGRAM_BOT_USERNAME && (
          <TelegramLoginButton botUsername={TELEGRAM_BOT_USERNAME} onAuth={handleLink} />
        )}
      </div>
      {(result || error) && (
        <p
          className={`mt-2 text-sm ${
            result?.type === "success" ? "text-success" : "text-danger"
          }`}
        >
          {error ?? result?.text}
        </p>
      )}
    </Card>
  );
}

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-fg">Настройки</h1>

      <Card>
        <CardHeader title="Профиль" />
        <div className="flex flex-col gap-3 text-sm">
          <div className="flex justify-between">
            <span className="text-fg-subtle">Имя</span>
            <span className="text-fg">{user?.name}</span>
          </div>
          {user?.email && (
            <div className="flex items-center justify-between gap-3">
              <span className="text-fg-subtle">Email</span>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <span className="text-fg">{user.email}</span>
                {user.is_email_verified ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Подтверждён
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
                    <AlertCircle className="h-3.5 w-3.5" />
                    Не подтверждён
                  </span>
                )}
              </div>
            </div>
          )}
          {user?.username && (
            <div className="flex justify-between">
              <span className="text-fg-subtle">Telegram</span>
              <span className="text-fg">@{user.username}</span>
            </div>
          )}
        </div>
      </Card>

      <EmailVerificationBlock />
      <BackupAccessBlock />
      <ManageEmailBlock />

      <Card variant="bordered">
        <CardHeader title="Тема оформления" subtitle="Выберите, как должен выглядеть кабинет" />
        <ThemeSwitcher />
      </Card>

      {user?.auth_type?.toUpperCase() === "EMAIL" && <ChangePasswordBlock />}
      {user?.auth_type?.toUpperCase() === "EMAIL" && <TelegramLinkBlock />}

      {/* Выход — для смены аккаунта (особенно на мобильном, где нет сайдбара) */}
      <Card variant="bordered">
        <CardHeader title="Аккаунт" subtitle="Выйти, чтобы войти под другим аккаунтом" />
        <Button
          variant="danger"
          onClick={handleLogout}
          className="w-full sm:w-auto"
        >
          <LogOut className="h-4 w-4" />
          Выйти из аккаунта
        </Button>
      </Card>
    </div>
  );
}
