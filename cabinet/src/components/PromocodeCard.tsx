import { useState } from "react";
import { Ticket, Check, Loader2 } from "lucide-react";
import { promocodeApi } from "@/api/promocode";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/types/api";
import { useT } from "@/i18n/I18nContext";

/** Ввод и активация промокода прямо в кабинете (награда применяется на бэке).
 *  onActivated — если передан, вызывается после успеха (мягкая перезагрузка данных
 *  страницы); иначе делаем полный reload, чтобы обновить подписку/баланс везде. */
export function PromocodeCard({ onActivated }: { onActivated?: () => void }) {
  const t = useT();
  const [code, setCode] = useState("");
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
    <Card>
      <CardHeader title={t("promo.title")} subtitle={t("promo.sub")} />
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
  );
}
