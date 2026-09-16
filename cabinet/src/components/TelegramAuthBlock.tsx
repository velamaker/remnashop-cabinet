import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";
import { TelegramLoginButton } from "@/components/TelegramLoginButton";
import { ApiError, type TelegramAuthRequest } from "@/types/api";

const TELEGRAM_BOT_USERNAME = import.meta.env.VITE_TELEGRAM_BOT_USERNAME || "";

/**
 * Вход через Telegram — кнопка + разделитель «или».
 *
 * ЗАЧЕМ ОТДЕЛЬНЫМ КОМПОНЕНТОМ. Этот вход был только на странице ВХОДА, а ссылка
 * приглашения ведёт на страницу РЕГИСТРАЦИИ (`/register?ref=…`), где его не было
 * вовсе. При том что телеграмом в кабинет заходят 1069 человек из 1090,
 * приглашённый попадал ровно туда, где нужной ему кнопки нет: либо уходил искать
 * вход сам, либо не доходил.
 *
 * Реф-код при этом не теряется в любом случае — его подхватывает
 * `captureReferralCode` при заходе на любую страницу и досчитывает после входа
 * (см. lib/referralRef.ts). Здесь чинится не атрибуция, а сам путь человека.
 *
 * ПОЧЕМУ ДВЕ КНОПКИ. Включён OIDC — показываем только его: классический Login
 * Widget в этом случае рендерит «Bot domain invalid», потому что требует
 * /setdomain у бота. Выключен — остаётся виджет.
 */
export function TelegramAuthBlock({
  next = "/",
  onError,
}: {
  next?: string;
  onError?: (message: string) => void;
}) {
  const t = useT();
  const navigate = useNavigate();
  const { loginWithTelegram } = useAuth();
  const { telegramOidcEnabled, emailAuthEnabled } = useBranding();

  if (!telegramOidcEnabled && !TELEGRAM_BOT_USERNAME) return null;

  const handleAuth = async (data: TelegramAuthRequest) => {
    try {
      await loginWithTelegram(data);
      navigate(next);
    } catch (err) {
      // 428 — «нужно согласие с документами». Через кнопку-виджет его не передать:
      // их ручка кладёт список принятых документов В ПРОВЕРКУ ПОДПИСИ, поэтому
      // запрос с ним ломает хэш и получает 401, а без него — тот же 428. Обойти
      // это на нашей стороне нечем, но и молчать нельзя: отправляем человека туда,
      // где согласие спросить можно — в бота (Mini App).
      if (err instanceof ApiError && err.status === 428) {
        onError?.(t("legal.viaBot"));
        return;
      }
      onError?.(err instanceof ApiError ? err.detail : t("login.errTelegramShort"));
    }
  };

  return (
    <>
      <div className="flex flex-col items-stretch gap-2.5">
        {telegramOidcEnabled ? (
          <button
            type="button"
            onClick={() => {
              window.location.href = "/api/auth/telegram/oidc/start";
            }}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[#2aabee] text-sm font-semibold text-white shadow-[0_10px_26px_-14px_rgba(42,171,238,0.9)] transition-colors hover:bg-[#1f97d4]"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current" aria-hidden>
              <path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z" />
            </svg>
            {t("login.viaTelegram")}
          </button>
        ) : (
          <div className="flex justify-center">
            <TelegramLoginButton botUsername={TELEGRAM_BOT_USERNAME} onAuth={handleAuth} />
          </div>
        )}
      </div>
      {/* Разделитель «или» — только когда под ним ЕСТЬ что разделять. Оператор
          может выключить вход по почте целиком: форма тогда не рисуется, и
          разделитель повисал над пустотой. */}
      {emailAuthEnabled && (
        <div className="my-6 flex items-center gap-3">
          <div className="h-px flex-1 bg-border-subtle" />
          <span className="mono-label text-fg-subtle">{t("common.or")}</span>
          <div className="h-px flex-1 bg-border-subtle" />
        </div>
      )}
    </>
  );
}
