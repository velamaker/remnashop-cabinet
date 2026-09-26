import { useEffect, useRef, useState } from "react";
import { Ticket, Check, Loader2 } from "lucide-react";
import { promocodeApi } from "@/api/promocode";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";
import { useBranding } from "@/contexts/BrandingContext";

const GIFT_CODE_RE = /^GIFT-[0-9A-F]{32}$/;

function forgetPromoParam() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.delete("promo");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch {
    /* адрес не поменять — не повод ронять успешную активацию */
  }
}

/** Ввод и активация промокода прямо в кабинете (награда применяется на бэке).
 *  onActivated — если передан, вызывается после успеха (мягкая перезагрузка данных
 *  страницы); иначе делаем полный reload, чтобы обновить подписку/баланс везде. */
export function PromocodeCard({ onActivated }: { onActivated?: () => void }) {
  const t = useT();
  // Промокоды живут в базе бота: гасим карточку в одном месте — она стоит и на
  // /billing, и на странице подписки.
  const { can } = useBranding();
  // Код из ссылки сертификата: `/subscription?promo=GIFT-…`. Читаем адрес напрямую,
  // а не через роутер — карточка стоит на двух страницах и в их тестах, и
  // зависимость от контекста роутера ей ни к чему. Только ПОДСТАВЛЯЕМ: активация —
  // кнопкой, как при ручном вводе (подарок может заменить текущий тариф).
  // Подставляем ТОЛЬКО подарочный код. Иначе ссылка на настоящий домен с плашкой
  // «код подарка подставлен» стала бы удобным способом подсунуть человеку чужой
  // промокод (например, меньшую персональную скидку, которая затрёт его большую).
  const [prefilled] = useState(() => {
    try {
      const raw = (new URLSearchParams(window.location.search).get("promo") ?? "").trim().toUpperCase();
      return GIFT_CODE_RE.test(raw) ? raw : "";
    } catch {
      return "";
    }
  });
  const [code, setCode] = useState(prefilled);
  const cardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Пришли по ссылке — показываем карточку, иначе она внизу страницы и её не видно.
    if (prefilled) cardRef.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [prefilled]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function rewardMessage(reward_type: string, reward: number | null): string {
    switch (reward_type) {
      case "DURATION":
        return t("promo.rewardDuration", { n: reward ?? 0 });
      case "TRAFFIC":
        return t("promo.rewardTraffic", { n: reward ?? 0 });
      case "DEVICES":
        return t("promo.rewardDevices", { n: reward ?? 0 });
      case "SUBSCRIPTION":
        return t("promo.rewardSubscription");
      case "PERSONAL_DISCOUNT":
      case "PURCHASE_DISCOUNT":
        return t("promo.rewardDiscount", { n: reward ?? 0 });
      default:
        return t("promo.applied");
    }
  }

  if (!can("promocode")) return null;

  const apply = async () => {
    const trimmed = code.trim();
    if (!trimmed || busy) return;
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      const r = await promocodeApi.activate(trimmed);
      setSuccess(rewardMessage(r.reward_type, r.reward));
      setCode("");
      // Код из ссылки израсходован — убираем его из адреса. Иначе после
      // перезагрузки данных карточка снова подставит тот же код с подсказкой
      // «нажмите Применить», и человек решит, что подарок не сработал.
      if (prefilled) forgetPromoParam();
      // Награда могла изменить подписку/скидку/баланс на других страницах.
      // Если хозяин страницы даёт колбэк — мягко обновляем его данные (карточка
      // с сообщением об успехе остаётся); иначе — полный reload после показа успеха.
      if (onActivated) onActivated();
      else setTimeout(() => window.location.reload(), 1600);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : t("promo.error"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={cardRef}>
    <Card>
      <CardHeader title={t("promo.title")} subtitle={t("promo.sub")} />
      {prefilled && !success && (
        <p className="mb-3 rounded-lg bg-accent/10 px-3 py-2 text-sm text-accent">{t("promo.fromGift")}</p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Ticket className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && apply()}
            placeholder={t("promo.placeholder")}
            maxLength={64}
            disabled={busy}
            className="h-10 w-full rounded-lg border border-[var(--border)] bg-bg-subtle pl-9 pr-3 text-sm uppercase tracking-wide text-fg outline-none transition-colors placeholder:normal-case placeholder:tracking-normal placeholder:text-fg-subtle focus:border-accent focus:ring-1 focus:ring-accent/30"
          />
        </div>
        <Button onClick={apply} disabled={busy || !code.trim()} size="lg">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : t("promo.apply")}
        </Button>
      </div>

      {success && (
        <p className="mt-3 flex items-center gap-2 text-sm font-medium text-success">
          <Check className="h-4 w-4 shrink-0" /> {success}
        </p>
      )}
      {error && <p className="mt-3 text-sm text-danger">{error}</p>}
    </Card>
    </div>
  );
}
