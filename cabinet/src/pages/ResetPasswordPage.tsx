import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { authApi } from "@/api/auth";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card } from "@/components/ui/Card";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { useT } from "@/i18n/I18nContext";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ApiError } from "@/types/api";

export default function ResetPasswordPage() {
  const t = useT();
  const navigate = useNavigate();
  const [step, setStep] = useState<"request" | "confirm">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const requestCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);
    try {
      await authApi.resetPasswordRequest(email.trim());
      setNotice(
        t("reset.noticeSent"),
      );
      setStep("confirm");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : t("reset.errSend"),
      );
    } finally {
      setIsLoading(false);
    }
  };

  const confirmReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t("reset.errMismatch"));
      return;
    }
    setIsLoading(true);
    try {
      await authApi.resetPasswordConfirm({ email: email.trim(), code: code.trim(), password });
      navigate("/login?reset=ok");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : t("reset.errChange"),
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="app-scroll bg-grain flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>

      <Card className="w-full max-w-sm animate-fade-in">
        <div className="mb-6 text-center">
          <h1 className="text-lg font-semibold text-fg">{t("reset.title")}</h1>
          <p className="mt-1 text-sm text-fg-subtle">
            {step === "request"
              ? t("reset.stepRequest")
              : t("reset.stepConfirm")}
          </p>
        </div>

        {notice && (
          <p className="mb-4 rounded-lg bg-accent/8 px-3 py-2 text-sm text-fg-muted">{notice}</p>
        )}

        {step === "request" ? (
          <form onSubmit={requestCode} className="flex flex-col gap-4">
            <Input
              label="Email"
              type="email"
              name="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            {error && <p className="text-sm text-danger">{error}</p>}
            <Button type="submit" isLoading={isLoading} className="mt-1 w-full">
              {t("reset.sendCode")}
            </Button>
          </form>
        ) : (
          <form onSubmit={confirmReset} className="flex flex-col gap-4">
            <Input
              label={t("reset.codeLabel")}
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <Input
              label={t("reset.newPassword")}
              type="password"
              name="new-password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Input
              label={t("reset.repeatPassword")}
              type="password"
              name="confirm-password"
              autoComplete="new-password"
              required
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {error && <p className="text-sm text-danger">{error}</p>}
            <Button type="submit" isLoading={isLoading} className="mt-1 w-full">
              {t("reset.changePassword")}
            </Button>
            <button
              type="button"
              onClick={() => { setStep("request"); setError(null); setNotice(null); }}
              className="text-center text-xs text-fg-subtle hover:text-fg"
            >
              {t("reset.resendCode")}
            </button>
          </form>
        )}

        <p className="mt-5 text-center text-sm text-fg-subtle">
          {t("reset.remembered")}{" "}
          <Link to="/login" className="font-medium text-accent hover:text-accent-hover">
            {t("login.submit")}
          </Link>
        </p>
      </Card>
    </div>
  );
}
