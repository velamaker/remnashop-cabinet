import { useCallback, useEffect, useRef, useState } from "react";
import { MonitorSmartphone } from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { useBranding } from "@/contexts/BrandingContext";
import { useT } from "@/i18n/I18nContext";
import { formatDate } from "@/lib/format";
import { endsBefore, newOffer, payOptions, slotViews } from "@/lib/extraDevice";
import { paymentsBlocked } from "@/lib/planChange";
import type { ExtraDeviceResponse } from "@/types/api";
import { ExtraDeviceOffer } from "./ExtraDeviceOffer";

/**
 * Страница «Устройства»: докупленные места и «Продлить».
 *
 * ПОЧЕМУ ЭТА ПАНЕЛЬ НЕ СЛУШАЕТ ТУМБЛЕР АПСЕЛЛА. Тумблер «Нужно больше устройств?»
 * выключает РЕКЛАМУ тарифа побольше. Продление уже оплаченного места — не реклама:
 * человек за него заплатил и должен видеть, до какой даты оно живёт и чем продлить.
 * Панель подчиняется только возможности `extra_device` и остановке платежей.
 *
 * Строку «Докупить устройство» панель показывает ТОЛЬКО когда её не показывает
 * карточка апселла (лимит ещё не заполнен или тумблер выключен) — иначе на одной
 * странице оказались бы две одинаковые кнопки.
 */
export function ExtraDevicesPanel({
  cardShowsOffer,
  onChanged,
}: {
  /** Карточка «Нужно больше устройств?» уже предлагает докупку — не дублируем. */
  cardShowsOffer: boolean;
  onChanged?: () => void;
}) {
  const t = useT();
  const { appearance, can } = useBranding();
  const [data, setData] = useState<ExtraDeviceResponse | null>(null);
  const asked = useRef(false);

  // Без оформления не знаем, что за бэкенд: `can` на пустом оформлении отвечает «да»,
  // и первый заход поверх «Бедолаги» сходил бы за несуществующей ручкой.
  const allowed =
    appearance != null && can("extra_device") && can("purchase") && !paymentsBlocked(appearance);

  const load = useCallback(() => {
    if (!allowed) return;
    subscriptionApi
      .extraDevice()
      .then(setData)
      .catch(() => {
        /* тихо: панель необязательная (404 у старого бота, сбой сети) */
      });
  }, [allowed]);

  useEffect(() => {
    if (!allowed || asked.current) return;
    asked.current = true;
    load();
  }, [allowed, load]);

  const reload = () => {
    load();
    onChanged?.();
  };

  if (!data?.enabled) return null;
  const slots = slotViews(data);
  const offer = newOffer(data);
  const canOfferHere = offer != null && !cardShowsOffer && payOptions(data, offer.amount).options.length > 0;
  if (slots.length === 0 && !canOfferHere) return null;

  return (
    <div className="rounded-xl border border-border-subtle bg-bg-subtle p-3">
      {slots.map(({ slot, extend }) => (
        <div key={slot.slot_id} className="flex items-start gap-3 py-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <MonitorSmartphone className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg">
              {t("extraDevice.slot", { date: formatDate(slot.ends_at) })}
            </p>
            {endsBefore(data, slot) && data.subscription_expire_at && (
              <p className="mt-0.5 text-xs text-fg-muted">
                {t("extraDevice.endsBefore", {
                  until: formatDate(data.subscription_expire_at),
                  date: formatDate(slot.ends_at),
                  limit: data.plan_device_limit ?? "",
                })}
              </p>
            )}
            {extend && (
              <ExtraDeviceOffer data={data} offer={extend} onChanged={reload} compact />
            )}
          </div>
        </div>
      ))}

      {canOfferHere && offer && (
        <div className={slots.length > 0 ? "mt-2 border-t border-border-subtle pt-2" : ""}>
          <p className="text-sm font-medium text-fg">
            {t("extraDevice.offer", { date: formatDate(offer.until) })}
          </p>
          <p className="tabular mt-0.5 text-xs text-fg-subtle">
            {t("extraDevice.price", {
              price: `${offer.amount} ${offer.symbol}`.trim(),
              days: offer.days,
            })}
          </p>
          <ExtraDeviceOffer data={data} offer={offer} onChanged={reload} compact />
        </div>
      )}
    </div>
  );
}
