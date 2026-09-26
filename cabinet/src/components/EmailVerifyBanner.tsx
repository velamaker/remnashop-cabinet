import { useEffect, useState } from "react";
import { Loader2, MailWarning } from "lucide-react";
import { authApi } from "@/api/auth";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";
import { ApiError } from "@/types/api";

/**
 * Пауза перед повторной отправкой кода, секунд.
 *
 * ПОЧЕМУ ЕСТЬ, ХОТЯ API ПАУЗЫ НЕ ОТДАЁТ. Ручка отправки кода стоит под
 * рейт-лимитом nginx (10 в минуту на адрес, ОБЩИЙ с вводом кода и входом), и
 * отказ он отдаёт как «повторите через минуту». Нетерпеливый человек, трижды
 * нажавший «прислать заново», упирался бы в этот лимит как раз тогда, когда
 * надо ввести пришедший код. Минута — ровно тот Retry-After, что шлёт nginx.
 */
export const RESEND_PAUSE_S = 60;

/**
 * Плашка «Подтвердите почту» на Главной.
 *
 * ЗАЧЕМ. Подтвердить почту можно было только в «Настройках», куда человек сам
 * не заходит, — а зарегистрированному по почте без подтверждения бот не даёт
 * купить или продлить подписку (почтовый гейт, по умолчанию включён). Человек
 * упирался в отказ на оплате, не понимая, где же его подтвердить. Теперь — прямо
 * на Главной: адрес, поле для кода, «Подтвердить» и «Прислать код заново». Ручки
 * те же, что у блока в настройках (/auth/email/request-verification и
 * /auth/email/confirm), новых нет.
 *
 * КОГДА ВИДНА. Есть почта, она не подтверждена, и бэкенд вообще умеет
 * подтверждение (возможность email_verify: поверх чужого бота её могут
 * выключить). После подтверждения плашка пропадает сразу, не дожидаясь, пока
 * перечитается профиль.
 *
 * Код сам не отправляем: плашка на Главной открывается при каждом заходе, и
 * письмо на каждый заход — это спам в ящик и лишний расход лимита.
 */
export function EmailVerifyBanner() {
  const t = useT();
  const { user, refreshMe } = useAuth();
  const { can } = useBranding();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"confirm" | "resend" | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [pause, setPause] = useState(0);
  const [confirmed, setConfirmed] = useState(false);

  // Обратный отсчёт — по секунде; таймер гасится при размонтировании.
  useEffect(() => {
    if (pause <= 0) return;
    const id = setTimeout(() => setPause((s) => s - 1), 1000);
    return () => clearTimeout(id);
  }, [pause]);

  if (!user?.email || user.is_email_verified || confirmed || !can("email_verify")) return null;
  const email = user.email;

  const resend = async () => {
    setBusy("resend");
    setMessage(null);
    try {
      const r = await authApi.requestEmailVerification();
      setMessage({ type: "success", text: t("emailBanner.sent", { email: r?.target_email || email }) });
      setPause(RESEND_PAUSE_S);
    } catch (e) {
      // Упёрлись в лимит — та же пауза, иначе кнопка звала бы нажать снова сразу.
      if (e instanceof ApiError && e.status === 429) setPause(RESEND_PAUSE_S);
      setMessage({ type: "error", text: e instanceof ApiError ? e.detail : t("set.errSendCode") });
    } finally {
      setBusy(null);
    }
  };

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    // Код не чистим до цифр: поверх чужого бота «код» бывает токеном из ссылки.
    const value = code.trim();
    if (!value) return;
    setBusy("confirm");
    setMessage(null);
    try {
      await authApi.confirmEmailVerification({ code: value });
      setConfirmed(true);
      // Профиль перечитываем, чтобы признак подтверждения увидели и другие экраны;
      // сама плашка уже скрыта и от этого ответа не зависит.
      void refreshMe().catch(() => {});
    } catch (err) {
      setMessage({ type: "error", text: err instanceof ApiError ? err.detail : t("set.wrongCode") });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-2xl border border-amber-400/40 bg-amber-400/10 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-amber-400/15 text-amber-500">
          <MailWarning className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fg">{t("emailBanner.title")}</p>
          <p className="mt-0.5 break-words text-sm text-fg-muted">
            {t("emailBanner.text", { email })}
          </p>
        </div>
      </div>

      <form onSubmit={confirm} className="mt-3 flex flex-col gap-2 sm:flex-row">
        <div className="min-w-0 flex-1">
          <Input
            name="email-verify-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder={t("emailBanner.codePlaceholder")}
            aria-label={t("emailBanner.codePlaceholder")}
            className="w-full"
          />
        </div>
        <Button
          type="submit"
          isLoading={busy === "confirm"}
          disabled={busy !== null || !code.trim()}
          className="whitespace-nowrap"
        >
          {t("set.confirm")}
        </Button>
      </form>

      {message && (
        <p className={`mt-2 text-sm ${message.type === "success" ? "text-success" : "text-danger"}`}>
          {message.text}
        </p>
      )}

      <div className="mt-3 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-fg-muted">{t("emailBanner.spam")}</p>
        <button
          type="button"
          onClick={resend}
          disabled={busy !== null || pause > 0}
          className="inline-flex items-center gap-1.5 self-start text-sm font-medium text-accent hover:underline disabled:cursor-not-allowed disabled:text-fg-subtle disabled:no-underline sm:self-auto"
        >
          {busy === "resend" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {pause > 0 ? t("emailBanner.resendIn", { s: pause }) : t("emailBanner.resend")}
        </button>
      </div>
    </div>
  );
}
