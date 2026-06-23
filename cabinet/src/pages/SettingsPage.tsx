import { useState } from "react";
import { CheckCircle2, AlertCircle } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
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

function TelegramLinkBlock() {
  const { user, refreshMe } = useAuth();
  const [error, setError] = useState<string | null>(null);

  if (!user || user.telegram_id) {
    return (
      <Card variant="bordered">
        <CardHeader title="Telegram" />
        <p className="text-sm text-fg-muted">
          {user?.telegram_id
            ? `Аккаунт привязан (ID: ${user.telegram_id})`
            : "Загрузка..."}
        </p>
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
      {TELEGRAM_BOT_USERNAME && (
        <TelegramLoginButton botUsername={TELEGRAM_BOT_USERNAME} onAuth={handleLink} />
      )}
      {error && <p className="mt-2 text-sm text-danger">{error}</p>}
    </Card>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();

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

      <Card variant="bordered">
        <CardHeader title="Тема оформления" subtitle="Выберите, как должен выглядеть кабинет" />
        <ThemeSwitcher />
      </Card>

      {user?.auth_type?.toUpperCase() === "EMAIL" && <ChangePasswordBlock />}
      {user?.auth_type?.toUpperCase() === "EMAIL" && <TelegramLinkBlock />}
    </div>
  );
}
