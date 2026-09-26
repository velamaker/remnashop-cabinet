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

/** sessionStorage: кому и когда в этой вкладке уже отправили код. */
export const SENT_KEY = "email_verify_code_sent";
/** sessionStorage: отправка ответила 503 — почта на установке выключена. */
export const OFF_KEY = "email_verify_unavailable";

interface SentMark {
  /** Адрес, для которого запрашивали код (pending_email || email). */
  to: string;
  /** Когда отправили, мс — от этого считается остаток паузы после перезагрузки. */
  at: number;
  /** Куда, по словам бэкенда, ушло письмо (target_email) — это и показываем. */
  shown: string;
}

// Хранилище может быть недоступно (приватный режим, запрет сайта) — тогда плашка
// просто не помнит отправку между перезагрузками, но работает.
function readSent(): SentMark | null {
  try {
    const raw = sessionStorage.getItem(SENT_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<SentMark> | null;
    if (!v || typeof v.to !== "string" || typeof v.at !== "number") return null;
    return { to: v.to, at: v.at, shown: typeof v.shown === "string" && v.shown ? v.shown : v.to };
  } catch {
    return null;
  }
}

function writeSent(mark: SentMark | null): void {
  try {
    if (mark) sessionStorage.setItem(SENT_KEY, JSON.stringify(mark));
    else sessionStorage.removeItem(SENT_KEY);
  } catch {
    /* хранилище недоступно — помним только до перезагрузки */
  }
}

function readOff(): boolean {
  try {
    return sessionStorage.getItem(OFF_KEY) === "1";
  } catch {
    return false;
  }
}

function writeOff(): void {
  try {
    sessionStorage.setItem(OFF_KEY, "1");
  } catch {
    /* хранилище недоступно — плашка спрячется до перезагрузки */
  }
}

/** Сколько секунд паузы осталось после отправки в момент `at`. */
function pauseLeft(at: number): number {
  const left = RESEND_PAUSE_S - Math.floor((Date.now() - at) / 1000);
  return Math.max(0, Math.min(RESEND_PAUSE_S, left));
}

/**
 * Плашка «Подтвердите почту» на Главной.
 *
 * ЗАЧЕМ. Подтвердить почту можно было только в «Настройках», куда человек сам
 * не заходит, — а зарегистрированному по почте без подтверждения бот не даёт
 * купить или продлить подписку (почтовый гейт, по умолчанию включён). Человек
 * упирался в отказ на оплате, не понимая, где же его подтвердить. Теперь — прямо
 * на Главной. Ручки те же, что у блока в настройках
 * (/auth/email/request-verification и /auth/email/confirm), новых нет.
 *
 * ДВА ШАГА. Код сам не отправляем: плашка на Главной открывается при каждом
 * заходе, и письмо на каждый заход — это спам в ящик и лишний расход лимита.
 * Поэтому сначала плашка только предлагает «Прислать код на {адрес}», а поле для
 * кода, «пришло на …» и подсказка про «Спам» появляются после успешной отправки.
 * Раньше плашка сразу просила ввести код из письма, которое никто не отправлял.
 * Факт отправки помним в sessionStorage: перезагрузка страницы, пока человек идёт
 * за письмом, не должна прятать поле для кода (и сбрасывать паузу повтора).
 *
 * АДРЕС — pending_email || email: при смене почты код уходит на новый адрес.
 *
 * КОГДА ВИДНА. Есть почта, она не подтверждена, бэкенд умеет подтверждение
 * (возможность email_verify: поверх чужого бота её могут выключить) и вход по
 * почте не выключен. Если отправка ответила 503 — почта на установке выключена
 * (SMTP не настроен или выключен в админке): звать подтверждать то, что прийти
 * не может, незачем — плашка прячется до конца сессии. После подтверждения
 * плашка пропадает сразу, не дожидаясь, пока перечитается профиль.
 */
export function EmailVerifyBanner() {
  const t = useT();
  const { user, refreshMe } = useAuth();
  const { can, emailAuthEnabled } = useBranding();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"confirm" | "send" | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [sent, setSent] = useState<SentMark | null>(readSent);
  const [pause, setPause] = useState(() => {
    const mark = readSent();
    return mark ? pauseLeft(mark.at) : 0;
  });
  const [off, setOff] = useState(readOff);
  const [confirmed, setConfirmed] = useState(false);

  // Обратный отсчёт — по секунде; таймер гасится при размонтировании.
  useEffect(() => {
    if (pause <= 0) return;
    const id = setTimeout(() => setPause((s) => s - 1), 1000);
    return () => clearTimeout(id);
  }, [pause]);

  if (
    !user?.email ||
    user.is_email_verified ||
    confirmed ||
    off ||
    emailAuthEnabled === false ||
    !can("email_verify")
  ) {
    return null;
  }
  const target = user.pending_email || user.email;
  // Код считается запрошенным только для ЭТОГО адреса: сменили почту — начинаем заново.
  const codeSent = sent != null && sent.to === target;

  const send = async () => {
    const again = codeSent;
    setBusy("send");
    setMessage(null);
    try {
      const r = await authApi.requestEmailVerification();
      const mark: SentMark = { to: target, at: Date.now(), shown: r?.target_email || target };
      writeSent(mark);
      setSent(mark);
      setPause(RESEND_PAUSE_S);
      // Первая отправка говорит сама за себя — появляется поле для кода; повтор —
      // отдельной строкой, иначе нажатие выглядело бы ничем не закончившимся.
      if (again) setMessage({ type: "success", text: t("emailBanner.sent", { email: mark.shown }) });
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        writeOff();
        setOff(true);
        return;
      }
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
      writeSent(null);
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

  const messageLine = message && (
    <p className={`mt-2 text-sm ${message.type === "success" ? "text-success" : "text-danger"}`}>
      {message.text}
    </p>
  );

  return (
    <div className="rounded-2xl border border-amber-400/40 bg-amber-400/10 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-amber-400/15 text-amber-500">
          <MailWarning className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          {codeSent ? (
            <>
              <p className="text-sm font-semibold text-fg">{t("emailBanner.title")}</p>
              <p className="mt-0.5 break-words text-sm text-fg-muted">
                {t("emailBanner.text", { email: sent.shown })}
              </p>
            </>
          ) : (
            <>
              <p className="break-words text-sm font-semibold text-fg">
                {t("emailBanner.askTitle", { email: target })}
              </p>
              <p className="mt-0.5 text-sm text-fg-muted">{t("emailBanner.askText")}</p>
            </>
          )}
        </div>
      </div>

      {!codeSent && (
        <>
          <Button
            type="button"
            onClick={send}
            isLoading={busy === "send"}
            disabled={busy !== null || pause > 0}
            className="mt-3 !h-auto min-h-9 w-full py-2 [overflow-wrap:anywhere] sm:w-auto"
          >
            {pause > 0 ? t("emailBanner.resendIn", { s: pause }) : t("emailBanner.send", { email: target })}
          </Button>
          {messageLine}
        </>
      )}

      {codeSent && (
        <>
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

          {messageLine}

          <div className="mt-3 flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-fg-muted">{t("emailBanner.spam")}</p>
            <button
              type="button"
              onClick={send}
              disabled={busy !== null || pause > 0}
              className="inline-flex items-center gap-1.5 self-start text-sm font-medium text-accent hover:underline disabled:cursor-not-allowed disabled:text-fg-subtle disabled:no-underline sm:self-auto"
            >
              {busy === "send" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {pause > 0 ? t("emailBanner.resendIn", { s: pause }) : t("emailBanner.resend")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
