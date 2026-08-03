import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { authApi } from "@/api/auth";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { useT } from "@/i18n/I18nContext";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { BrandLogo } from "@/components/BrandLogo";
import { BrandWordmark } from "@/components/BrandWordmark";
import { ApiError } from "@/types/api";

/**
 * Подтверждение почты по ссылке из письма.
 *
 * Наш бэкенд присылает код и подтверждение происходит в настройках. Но кабинет
 * умеет работать и поверх чужого бота, а тот шлёт письмо со ССЫЛКОЙ сюда —
 * `/verify-email?token=…`. Без этой страницы человек с такой ссылкой упирался бы
 * в пустоту. Токен из адреса уходит в ту же ручку, что и код.
 */
export default function VerifyEmailPage() {
  const t = useT();
  const [params] = useSearchParams();
  const token = (params.get("token") ?? "").trim();
  const [state, setState] = useState<"loading" | "ok" | "fail">(token ? "loading" : "fail");
  const [message, setMessage] = useState<string | null>(
    token ? null : t("verifyEmail.noToken"),
  );
  // Строгий режим React в разработке монтирует компонент дважды — без этого
  // одноразовый токен сгорал бы на первом же вызове, а человек видел ошибку.
  const sent = useRef(false);

  useEffect(() => {
    if (!token || sent.current) return;
    sent.current = true;
    authApi
      .confirmEmailVerification({ code: token })
      .then(() => setState("ok"))
      .catch((err) => {
        setState("fail");
        setMessage(err instanceof ApiError ? err.detail : t("verifyEmail.failed"));
      });
  }, [token, t]);

  return (
    <div className="app-scroll h-full bg-bg">
      <div className="bg-grain relative flex min-h-full flex-col overflow-x-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="hairline-grid absolute inset-0" />
          <div
            className="aurora left-1/2 top-[-15rem] h-[30rem] w-[30rem] -translate-x-1/2"
            style={{ background: "radial-gradient(circle, var(--accent-glow) 0%, transparent 68%)" }}
          />
        </div>

        <header className="relative z-20 flex w-full items-center justify-between gap-4 px-5 py-4 sm:px-8 sm:py-5">
          <Link to="/" className="flex min-w-0 items-center gap-2.5">
            <BrandLogo size={30} />
            <BrandWordmark className="text-[17px]" showSuffix={false} />
          </Link>
          <div className="flex items-center gap-2">
            <LanguageSwitcher />
            <ThemeSwitcher />
          </div>
        </header>

        <main className="relative z-10 mx-auto flex w-full max-w-[26rem] flex-1 flex-col justify-center px-5 pb-12 pt-4 sm:pb-16 sm:pt-6">
          <div className="panel panel-sheen p-6 text-center sm:p-7">
            {state === "loading" && (
              <>
                <Loader2 className="mx-auto h-8 w-8 animate-spin text-accent" />
                <p className="mt-4 text-sm text-fg-muted">{t("verifyEmail.checking")}</p>
              </>
            )}
            {state === "ok" && (
              <>
                <CheckCircle2 className="mx-auto h-9 w-9 text-success" />
                <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">
                  {t("verifyEmail.okTitle")}
                </h1>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">
                  {t("verifyEmail.okText")}
                </p>
              </>
            )}
            {state === "fail" && (
              <>
                <XCircle className="mx-auto h-9 w-9 text-danger" />
                <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">
                  {t("verifyEmail.failTitle")}
                </h1>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">
                  {message ?? t("verifyEmail.failed")}
                </p>
              </>
            )}
            <Link
              to="/settings"
              className="btn-hero mt-6 inline-flex h-11 w-full items-center justify-center rounded-xl text-[15px] font-semibold"
            >
              {t("verifyEmail.toSettings")}
            </Link>
          </div>
        </main>
      </div>
    </div>
  );
}
