import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";
import { LegalConsent } from "@/components/LegalConsent";
import { ApiError } from "@/types/api";

/**
 * Согласие с документами для входа через Telegram Mini App.
 *
 * ЗАЧЕМ ОТДЕЛЬНЫМ ЭКРАНОМ, А НЕ ГАЛОЧКАМИ ЗАРАНЕЕ. В Mini App вход происходит
 * САМ, без формы: кабинет открывается внутри телеграма и сразу отправляет
 * `init_data`. Спрашивать согласие до этого значило бы задерживать всех ради
 * установок, где оно вообще не требуется. Поэтому сначала обычная попытка, и
 * только если бэкенд ответил «нужно согласие» (428) — этот экран.
 *
 * ПОЧЕМУ ЭТО ВАЖНЕЕ, ЧЕМ КАЖЕТСЯ. Телеграмом в кабинет заходят почти все. Пока
 * согласие умела слать только форма почты, обязательное согласие закрывало
 * регистрацию подавляющему большинству — и человек видел не форму, а строку
 * ошибки без единой кнопки.
 */
export function TelegramConsentRetry({
  initData,
  onDone,
}: {
  initData: string;
  onDone: () => void;
}) {
  const t = useT();
  const { loginWithTelegramWebApp } = useAuth();
  const { legalDocuments, refresh } = useBranding();

  // Бэкенд сказал «нужно согласие», а списка документов у нас нет: значит
  // оформление читалось в момент, когда их ручка не ответила, и «согласие не
  // требуется» попало в кэш на минуту. Без этого перечитывания человек увидел бы
  // экран без единой галочки, нажал бы кнопку и получил тот же отказ по кругу.
  useEffect(() => {
    if (!legalDocuments.length) void refresh();
    // один раз на появление экрана: дальше список либо есть, либо у бэкенда беда
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [keys, setKeys] = useState<string[]>([]);
  const [allAccepted, setAllAccepted] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!allAccepted) {
      setError(t("legal.required"));
      return;
    }
    setError(null);
    setIsLoading(true);
    try {
      await loginWithTelegramWebApp({ init_data: initData, accepted_legal_documents: keys });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t("login.errTelegramShort"));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <LegalConsent
        onChange={(accepted, all) => {
          setKeys(accepted);
          setAllAccepted(all);
        }}
      />
      {error && <p className="text-[13px] leading-relaxed text-danger">{error}</p>}
      <button
        type="button"
        onClick={submit}
        disabled={isLoading}
        className="btn-hero inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl text-[15px] font-semibold disabled:cursor-not-allowed disabled:opacity-60"
      >
        {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        {t("login.viaTelegram")}
      </button>
    </div>
  );
}
