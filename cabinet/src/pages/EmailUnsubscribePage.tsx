import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CheckCircle2, Loader2, MailX, XCircle } from "lucide-react";
import { emailOptoutApi } from "@/api/emailOptout";
import { ThemeSwitcher } from "@/components/ui/ThemeSwitcher";
import { useT } from "@/i18n/I18nContext";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { BrandLogo } from "@/components/BrandLogo";
import { BrandWordmark } from "@/components/BrandWordmark";
import { ApiError } from "@/types/api";

type OptoutState = "loading" | "ask" | "done" | "resubscribed" | "invalid" | "failed";

/** Ссылка не наша или испорчена — повторять бесполезно.
 *  400 — подпись не сошлась; 404 — бэкенд без этой ручки; 501 — кабинет поверх
 *  чужого бота, где писем сводки нет вовсе. */
const isInvalidLink = (e: unknown) =>
  e instanceof ApiError && (e.status === 400 || e.status === 404 || e.status === 501);

/**
 * Отписка от месячной сводки по ссылке из письма — без входа.
 *
 * ПОЧЕМУ КНОПКА, А НЕ ОТПИСКА ПРИ ОТКРЫТИИ. Ссылки из писем открывают не только
 * люди: корпоративные фильтры и антивирусы заходят по ним сами. Отписывай
 * открытие — сводка молча выключалась бы у всех, чья почта проходит через такой
 * фильтр. Поэтому при открытии только чтение, а отписка — нажатием.
 *
 * Логика вынесена в `EmailOptoutPanel`, чтобы её можно было проверить без
 * оформления страницы.
 */
export function EmailOptoutPanel({ token }: { token: string }) {
  const t = useT();
  const [state, setState] = useState<OptoutState>(token ? "loading" : "invalid");
  const [busy, setBusy] = useState(false);
  // Строгий режим React монтирует компонент дважды — второй GET ни к чему.
  const asked = useRef(false);

  useEffect(() => {
    if (!token || asked.current) return;
    asked.current = true;
    emailOptoutApi
      .status(token)
      .then((r) => setState(r.subscribed ? "ask" : "done"))
      .catch((e) => setState(isInvalidLink(e) ? "invalid" : "failed"));
  }, [token]);

  const act = async (subscribe: boolean) => {
    setBusy(true);
    try {
      if (subscribe) {
        await emailOptoutApi.resubscribe(token);
        setState("resubscribed");
      } else {
        await emailOptoutApi.unsubscribe(token);
        setState("done");
      }
    } catch (e) {
      setState(isInvalidLink(e) ? "invalid" : "failed");
    } finally {
      setBusy(false);
    }
  };

  const button =
    "btn-hero mt-6 inline-flex h-11 w-full items-center justify-center rounded-xl text-[15px] font-semibold disabled:opacity-60";

  return (
    <div className="panel panel-sheen p-6 text-center sm:p-7">
      {state === "loading" && (
        <>
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-accent" />
          <p className="mt-4 text-sm text-fg-muted">{t("common.loading")}</p>
        </>
      )}
      {state === "ask" && (
        <>
          <MailX className="mx-auto h-9 w-9 text-accent" />
          <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">{t("emailOptout.title")}</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">{t("emailOptout.intro")}</p>
          <button type="button" className={button} disabled={busy} onClick={() => act(false)}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("emailOptout.unsubscribe")}
          </button>
        </>
      )}
      {state === "done" && (
        <>
          <CheckCircle2 className="mx-auto h-9 w-9 text-success" />
          <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">{t("emailOptout.doneTitle")}</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">{t("emailOptout.doneText")}</p>
          <button type="button" className={button} disabled={busy} onClick={() => act(true)}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("emailOptout.resubscribe")}
          </button>
        </>
      )}
      {state === "resubscribed" && (
        <>
          <CheckCircle2 className="mx-auto h-9 w-9 text-success" />
          <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">{t("emailOptout.title")}</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">{t("emailOptout.resubscribedText")}</p>
        </>
      )}
      {(state === "invalid" || state === "failed") && (
        <>
          <XCircle className="mx-auto h-9 w-9 text-danger" />
          <h1 className="font-display mt-4 text-[1.35rem] leading-tight text-fg">{t("emailOptout.title")}</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">
            {t(state === "invalid" ? "emailOptout.invalid" : "emailOptout.failed")}
          </p>
        </>
      )}
      <Link to="/" className="mt-5 inline-block text-sm font-medium text-accent hover:underline">
        {t("nav.home")}
      </Link>
    </div>
  );
}

export default function EmailUnsubscribePage() {
  const [params] = useSearchParams();
  const token = (params.get("t") ?? "").trim();

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
          <EmailOptoutPanel token={token} />
        </main>
      </div>
    </div>
  );
}
