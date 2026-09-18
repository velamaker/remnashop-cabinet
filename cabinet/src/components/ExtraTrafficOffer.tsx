import { useState } from "react";
import { Gauge, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { subscriptionApi } from "@/api/subscription";
import { Button } from "@/components/ui/Button";
import { useT } from "@/i18n/I18nContext";
import { formatDateTime } from "@/lib/format";
import { newRequestId } from "@/lib/bulkJobs";
import { leftGb, payOptions, type ExtraTrafficOfferView } from "@/lib/extraTraffic";
import type { ExtraTrafficResponse } from "@/types/api";

/**
 * Карточка «Мало трафика? +N ГБ за X ₽» с подтверждением суммы.
 *
 * ДВА ШАГА НАМЕРЕННО. Первое нажатие показывает, что именно спишется, сколько ГБ
 * добавится и ДО КАКОГО МОМЕНТА прибавка живёт; второе — платит. Срок здесь не
 * мелкий текст: прибавка живёт до ближайшего обновления трафика, а не до конца
 * подписки, и человек должен узнать это ДО оплаты, а не после.
 *
 * `request_id` генерируется ОДИН РАЗ на подтверждение и переживает повторные
 * нажатия: двойной клик и повтор после разорванного соединения дают одну прибавку,
 * а не две — это держит сервер по тому же ключу.
 *
 * Кнопки, которой нечем оплатить, здесь не бывает: `payOptions` вернёт пустой
 * список, и компонент не нарисуется вовсе (см. lib/extraTraffic).
 */
export function ExtraTrafficOffer({
  data,
  offer,
  onChanged,
  compact = false,
}: {
  data: ExtraTrafficResponse;
  offer: ExtraTrafficOfferView;
  /** Перечитать подписку и предложение: лимит и расход изменились. */
  onChanged?: () => void;
  compact?: boolean;
}) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);

  const pay = payOptions(data, offer.price);
  if (pay.options.length === 0) return null;

  const priceText = `${offer.price} ${offer.symbol}`.trim();
  const until = offer.until ? formatDateTime(offer.until) : "";
  const untilLine = offer.until
    ? t("extraTraffic.until", { date: until })
    : t("extraTraffic.untilNoReset");

  const ensureRequestId = () => {
    if (requestId) return requestId;
    // newRequestId, а не crypto.randomUUID: последнего нет в незащищённом контексте
    // (превью по http, локальная сборка), и кнопка оплаты там просто не работала.
    const fresh = newRequestId();
    setRequestId(fresh);
    return fresh;
  };

  const start = () => {
    setNote(null);
    ensureRequestId();
    setConfirming(true);
  };

  const buy = async (how: "balance" | "gateway") => {
    if (busy) return; // повторный клик во время запроса не шлёт второй платёж
    setBusy(true);
    setNote(null);
    try {
      const result = await subscriptionApi.buyExtraTraffic({
        request_id: ensureRequestId(),
        pay: how,
        gateway_type: how === "gateway" ? (pay.gateway ?? undefined) : undefined,
        expected_amount: offer.price,
        expected_gb: offer.gb,
      });
      if (result.result === "applied") {
        setConfirming(false);
        const limit = result.traffic_limit_gb ?? "";
        const date = result.until ? formatDateTime(result.until) : until;
        setNote(
          result.unlocked
            ? t("extraTraffic.doneUnlocked", { limit, date })
            : t("extraTraffic.done", { limit, date }),
        );
        onChanged?.();
        return;
      }
      if (result.result === "pending" && result.payment_url) {
        window.location.href = result.payment_url;
        return;
      }
      if (result.result === "price_changed") {
        // Владелец поменял цену или объём между показом и нажатием. Новый ключ не
        // берём: человек подтверждает заново, и это осознанно другая покупка.
        const fresh = result.quote;
        setNote(
          t("extraTraffic.errPrice", {
            gb: fresh?.gb ?? offer.gb,
            price: `${fresh?.price ?? ""} ${offer.symbol}`.trim(),
          }),
        );
        setRequestId(null);
        onChanged?.();
        return;
      }
      if (result.result === "insufficient_balance") {
        setNote(
          t("extraTraffic.balanceLow", { balance: `${result.balance ?? ""} ${offer.symbol}`.trim() }),
        );
        return;
      }
      setNote(t("extraTraffic.errFailed"));
      onChanged?.();
    } catch {
      // Ошибку сети и 502 показываем одинаково: деньги в обоих случаях не списаны.
      setNote(t("extraTraffic.errFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={compact ? "mt-2" : "mt-3"}>
      {!confirming && (
        <Button type="button" size={compact ? "sm" : "md"} onClick={start} className="w-full sm:w-auto">
          <Plus className="h-4 w-4" />
          {t("extraTraffic.offer", { gb: offer.gb, price: priceText })}
        </Button>
      )}

      {confirming && (
        <div className="rounded-xl border border-accent/30 bg-bg-subtle p-3">
          <p className="text-sm font-medium text-fg">
            {offer.until
              ? t("extraTraffic.confirm", { gb: offer.gb, price: priceText, date: until })
              : `${t("extraTraffic.offer", { gb: offer.gb, price: priceText })} — ${t("extraTraffic.untilNoReset")}`}
          </p>
          {/* Меньше суток до обновления — отдельной строкой, а не мелким текстом:
              человек имеет право передумать и просто дождаться обновления. */}
          {offer.endingSoon && offer.until && (
            <p className="mt-1 text-xs font-medium text-warning">
              {t("extraTraffic.soon", { date: until })}
            </p>
          )}
          {pay.balanceLow && (
            <p className="mt-2 text-xs text-fg-subtle">
              {t("extraTraffic.balanceLow", { balance: `${data.balance ?? ""} ${offer.symbol}`.trim() })}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {pay.options.includes("balance") && (
              <Button type="button" size="sm" isLoading={busy} onClick={() => buy("balance")}>
                {t("extraTraffic.payBalance")}
              </Button>
            )}
            {pay.options.includes("gateway") && (
              <Button
                type="button"
                size="sm"
                variant={pay.options.includes("balance") ? "secondary" : "primary"}
                isLoading={busy}
                onClick={() => buy("gateway")}
              >
                {t("extraTraffic.payGateway", { price: priceText })}
              </Button>
            )}
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}

      {!confirming && <p className="mt-1.5 text-xs text-fg-subtle">{untilLine}</p>}
      {note && <p className="mt-2 text-xs text-fg-muted">{note}</p>}
    </div>
  );
}

/** Заголовок предложения: сколько трафика осталось и что уже докуплено. */
export function ExtraTrafficHeadline({ data }: { data: ExtraTrafficResponse }) {
  const t = useT();
  const left = leftGb(data);
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-accent">
        <Gauge className="h-5 w-5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-base font-bold text-fg">{t("extraTraffic.title")}</p>
        {left != null && data.traffic_limit_gb != null && (
          <p className="tabular mt-0.5 text-sm text-fg">
            {t("extraTraffic.left", { left, limit: data.traffic_limit_gb })}
          </p>
        )}
        {(data.extra_gb_active ?? 0) > 0 && (
          <p className="tabular mt-0.5 text-xs text-fg-subtle">
            {t("extraTraffic.active", { gb: data.extra_gb_active ?? 0 })}
          </p>
        )}
        <Link to="/billing" className="mt-0.5 inline-block text-xs text-accent hover:underline">
          {t("extraTraffic.orPlan")}
        </Link>
      </div>
    </div>
  );
}
