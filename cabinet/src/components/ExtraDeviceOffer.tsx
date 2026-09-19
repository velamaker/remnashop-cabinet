import { useEffect, useState } from "react";
import { MonitorSmartphone, Plus } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { Button } from "@/components/ui/Button";
import { useT } from "@/i18n/I18nContext";
import { formatDate } from "@/lib/format";
import { newRequestId } from "@/lib/bulkJobs";
import { payOptions, type ExtraDeviceOfferView } from "@/lib/extraDevice";
import { onReturnFromPayment, openPayment } from "@/lib/payment";
import type { ExtraDeviceResponse } from "@/types/api";

/**
 * Кнопка «Докупить устройство» с подтверждением суммы.
 *
 * ДВА ШАГА НАМЕРЕННО. Первое нажатие показывает, что именно спишется и до какой даты,
 * второе — платит. Между ними человек видит цену ещё раз: покупка одним кликом на
 * сумму, которую он не называл, — не то, чего ждут от кнопки рядом со списком устройств.
 *
 * `request_id` генерируется ОДИН РАЗ на подтверждение и переживает повторные нажатия:
 * двойной клик и повтор после разорванного соединения дают одно место, а не два — это
 * держит сервер по тому же ключу.
 *
 * Кнопки, которой нечем оплатить, здесь не бывает: `payOptions` вернёт пустой список,
 * и компонент не нарисуется вовсе (см. lib/extraDevice).
 */
export function ExtraDeviceOffer({
  data,
  offer,
  onChanged,
  compact = false,
}: {
  data: ExtraDeviceResponse;
  offer: ExtraDeviceOfferView;
  /** Перечитать устройства и предложение: лимит и список мест изменились. */
  onChanged?: () => void;
  compact?: boolean;
}) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  // Счёт открыт во внешнем окне: ждём возвращения, чтобы перечитать места —
  // редирект платёжки до мини-аппы не доезжает.
  const [awaitingReturn, setAwaitingReturn] = useState(false);

  useEffect(() => {
    if (!awaitingReturn) return;
    return onReturnFromPayment(() => onChanged?.());
  }, [awaitingReturn, onChanged]);

  const pay = payOptions(data, offer.amount);
  if (pay.options.length === 0) return null;

  const priceText = `${offer.amount} ${offer.symbol}`.trim();
  const until = formatDate(offer.until);

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
      const result = await subscriptionApi.buyExtraDevice({
        request_id: ensureRequestId(),
        kind: offer.kind,
        slot_id: offer.slotId,
        pay: how,
        gateway_type: how === "gateway" ? (pay.gateway ?? undefined) : undefined,
        expected_amount: offer.amount,
      });
      if (result.result === "applied") {
        setConfirming(false);
        setNote(
          t("extraDevice.done", {
            max: result.device_limit ?? "",
            date: result.until ? formatDate(result.until) : until,
          }),
        );
        onChanged?.();
        return;
      }
      if (result.result === "pending" && result.payment_url) {
        // В мини-аппе — наружу: иначе WebView уходит на платёжку и «назад» некуда.
        if (openPayment(result.payment_url)) {
          setConfirming(false);
          setNote(t("payment.openedExternally"));
          setAwaitingReturn(true);
        }
        return;
      }
      if (result.result === "price_changed") {
        // Подписку успели продлить — период вырос вместе с ценой. Новый ключ не
        // берём: человек подтверждает заново, и это осознанно другая покупка.
        const fresh = result.quote?.new?.amount;
        setNote(t("extraDevice.errPrice", { price: `${fresh ?? ""} ${offer.symbol}`.trim() }));
        setRequestId(null);
        onChanged?.();
        return;
      }
      if (result.result === "insufficient_balance") {
        setNote(t("extraDevice.balanceLow", { balance: `${result.balance ?? ""} ${offer.symbol}`.trim() }));
        return;
      }
      setNote(t("extraDevice.errFailed"));
      onChanged?.();
    } catch {
      // Ошибку сети и 502 показываем одинаково: деньги в обоих случаях не списаны.
      setNote(t("extraDevice.errFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={compact ? "mt-2" : "mt-3"}>
      {!confirming && (
        <Button
          type="button"
          size={compact ? "sm" : "md"}
          onClick={start}
          className="w-full sm:w-auto"
        >
          {offer.kind === "extend" ? (
            <>{t("extraDevice.extend", { date: until, price: priceText })}</>
          ) : (
            <>
              <Plus className="h-4 w-4" />
              {t("extraDevice.buy", { price: priceText })}
            </>
          )}
        </Button>
      )}

      {confirming && (
        <div className="rounded-xl border border-accent/30 bg-bg-subtle p-3">
          <p className="text-sm font-medium text-fg">
            {t("extraDevice.confirm", { date: until, price: priceText })}
          </p>
          <p className="mt-1 text-xs text-fg-muted">{t("extraDevice.note")}</p>
          {data.removes_excess && (
            <p className="mt-1 text-xs text-fg-muted">{t("extraDevice.endsNote")}</p>
          )}
          {pay.balanceLow && (
            <p className="mt-2 text-xs text-fg-subtle">
              {t("extraDevice.balanceLow", { balance: `${data.balance ?? ""} ${offer.symbol}`.trim() })}
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {pay.options.includes("balance") && (
              <Button type="button" size="sm" isLoading={busy} onClick={() => buy("balance")}>
                {t("extraDevice.payBalance")}
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
                {t("extraDevice.payGateway", { price: priceText })}
              </Button>
            )}
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}

      {note && <p className="mt-2 text-xs text-fg-muted">{note}</p>}
    </div>
  );
}

/** Заголовок предложения: «+1 устройство до DD.MM» и цена за остаток. */
export function ExtraDeviceHeadline({ offer }: { offer: ExtraDeviceOfferView }) {
  const t = useT();
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-accent">
        <MonitorSmartphone className="h-5 w-5" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-base font-bold text-fg">{t("extraDevice.title")}</p>
        <p className="mt-0.5 text-sm text-fg">
          {t("extraDevice.offer", { date: formatDate(offer.until) })}
        </p>
        <p className="tabular mt-0.5 text-xs text-fg-subtle">
          {t("extraDevice.price", {
            price: `${offer.amount} ${offer.symbol}`.trim(),
            days: offer.days,
          })}
        </p>
      </div>
    </div>
  );
}
