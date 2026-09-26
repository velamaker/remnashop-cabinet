import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CheckCircle2, Gift, Loader2, Send, UserRound, XCircle } from "lucide-react";
import { giftApi, type GiftCertificate } from "@/api/gift";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { BrandLogo } from "@/components/BrandLogo";
import { BrandWordmark } from "@/components/BrandWordmark";
import { useAuth } from "@/contexts/AuthContext";
import { useT } from "@/i18n/I18nContext";
import { ApiError } from "@/types/api";

type View = "loading" | "missing" | "failed" | GiftCertificate;

/**
 * Подарочный сертификат по ссылке — без входа.
 *
 * ЗАЧЕМ. Подарок и раньше был кодом, но получателю приходилось самому искать в
 * боте раздел «Промокод» и набирать 37 символов. Ссылку же просто пересылают: по
 * ней видно, что подарено, и активация — одно нажатие. В боте — штатный deep link
 * `?start=promo_<код>` (с двойным подтверждением, если у человека уже есть
 * подписка), в кабинете — поле промокода с уже подставленным кодом.
 *
 * ПОЧЕМУ НЕ АКТИВИРУЕМ ПРИ ОТКРЫТИИ. Ссылку открывают и превью мессенджеров, и
 * антивирусы. Активируй открытие — подарок забрал бы робот. Поэтому только кнопкой.
 *
 * Слово «VPN» до входа не пишем (политика витрины): только бренд и «подписка».
 */
export function GiftCertificatePanel({ code }: { code: string }) {
  const t = useT();
  const { user } = useAuth();
  const [view, setView] = useState<View>(code ? "loading" : "missing");
  // Строгий режим React монтирует дважды — второй запрос ни к чему.
  const asked = useRef(false);

  useEffect(() => {
    if (!code || asked.current) return;
    asked.current = true;
    giftApi
      .certificate(code)
      .then(setView)
      .catch((e) => setView(e instanceof ApiError && e.status === 404 ? "missing" : "failed"));
  }, [code]);

  const daysLabel = typeof view === "object" ? t("gift.cert.days", { n: view.days }) : "";
  // В кабинете: поле промокода на странице тарифов, код уже подставлен. Именно
  // /billing, а не /subscription: у человека БЕЗ подписки страница подписки
  // показывает только «оформите тариф» без поля промокода — а это и есть главный
  // получатель подарка. Гостя по дороге ждёт вход или регистрация, и код едет в
  // ?next=: ProtectedRoute отправляет на /login?next=…, страницы входа и
  // регистрации передают next друг другу и в каждый способ входа — почту, виджет
  // Telegram, Mini App и OIDC (тот уходит на сервер и держит next в своей
  // tx-куке). Все эти пути заперты тестом GiftCertificate.authNext.test.tsx;
  // появится новый способ входа — проведи next и через него, иначе код потеряется.
  const cabinetTarget = `/billing?promo=${encodeURIComponent(code)}`;

  const primary =
    "btn-gradient inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-semibold text-white";
  const secondary =
    "inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-[var(--border)] bg-bg-raised text-[15px] font-medium text-fg transition-colors hover:border-accent";

  return (
    <div className="card-hero p-6 text-center sm:p-7">
      {view === "loading" && (
        <>
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-accent" />
          <p className="mt-4 text-sm text-fg-muted">{t("common.loading")}</p>
        </>
      )}

      {(view === "missing" || view === "failed") && (
        <>
          <XCircle className="mx-auto h-9 w-9 text-danger" />
          <h1 className="mt-4 text-[1.35rem] font-semibold leading-tight text-fg">{t("gift.cert.title")}</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">
            {t(view === "missing" ? "gift.cert.missing" : "gift.cert.failed")}
          </p>
        </>
      )}

      {typeof view === "object" && (
        <>
          {view.state === "activated" ? (
            <CheckCircle2 className="mx-auto h-10 w-10 text-success" />
          ) : (
            <Gift className={`mx-auto h-10 w-10 ${view.state === "void" ? "text-fg-subtle" : "text-accent"}`} />
          )}
          <p className="mt-4 font-mono text-[11px] font-medium uppercase leading-none tracking-[0.2em] text-accent">
            {t("gift.cert.kicker")}
          </p>
          <h1 className="mt-2 text-[1.5rem] font-semibold leading-tight text-fg">{view.plan_name}</h1>
          <p className="mt-1 text-[15px] font-medium text-fg-muted">{daysLabel}</p>

          {view.state === "ready" && (
            <>
              <p className="mt-4 text-sm leading-relaxed text-fg-muted">{t("gift.cert.ready")}</p>
              <div className="mt-6 flex flex-col gap-2.5">
                {view.bot_url && (
                  <a href={view.bot_url} target="_blank" rel="noopener noreferrer" className={primary}>
                    <Send className="h-4 w-4" />
                    {t("gift.cert.inTelegram")}
                  </a>
                )}
                <Link to={cabinetTarget} className={view.bot_url ? secondary : primary}>
                  <UserRound className="h-4 w-4" />
                  {t("gift.cert.inCabinet")}
                </Link>
                {!user && (
                  <Link
                    to={`/register?next=${encodeURIComponent(cabinetTarget)}`}
                    className="mt-1 text-sm font-medium text-accent hover:underline"
                  >
                    {t("gift.cert.noAccount")}
                  </Link>
                )}
              </div>
            </>
          )}

          {view.state === "activated" && (
            <p className="mt-4 text-sm leading-relaxed text-fg-muted">{t("gift.cert.activated")}</p>
          )}

          {view.state === "void" && (
            <p className="mt-4 text-sm leading-relaxed text-fg-muted">{t("gift.cert.void")}</p>
          )}

          <p className="mt-5 font-mono text-[11px] tracking-wide text-fg-subtle">{view.code}</p>
        </>
      )}

      <Link to="/" className="mt-5 inline-block text-sm font-medium text-accent hover:underline">
        {t("nav.home")}
      </Link>
    </div>
  );
}

export default function GiftCertificatePage() {
  const { code = "" } = useParams();

  return (
    <div className="app-scroll h-full bg-bg">
      <div className="relative flex min-h-full flex-col overflow-x-hidden">
        {/* Свечение делаем утилитами, а не своим классом: страницу сертификата
            открывает посторонний человек в ЛЮБОЙ установке продукта, и она не должна
            зависеть от примитивов оформления, которых в этой установке может не быть. */}
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div
            className="absolute left-1/2 top-[-15rem] h-[30rem] w-[30rem] -translate-x-1/2 rounded-full opacity-50 blur-3xl"
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
          <GiftCertificatePanel code={code.trim().toUpperCase()} />
        </main>
      </div>
    </div>
  );
}
