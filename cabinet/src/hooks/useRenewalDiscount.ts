import { useEffect, useState } from "react";
import { renewalDiscountApi, type RenewalDiscountStatus } from "@/api/renewalDiscount";

/**
 * Действующая скидка на продление. `null` — скидки нет ИЛИ узнать не удалось.
 *
 * Любая ошибка (404/501 у чужого бэкенда, сеть) — это `null`, а не сообщение:
 * скидка — необязательное дополнение к плашке продления, и её отсутствие не
 * должно ни ронять Главную, ни рисовать ошибку на месте предложения.
 * `enabled=false` — не спрашиваем вовсе (нет подписки или пробный период).
 */
export function useRenewalDiscount(enabled: boolean): RenewalDiscountStatus | null {
  const [offer, setOffer] = useState<RenewalDiscountStatus | null>(null);

  useEffect(() => {
    if (!enabled) {
      setOffer(null);
      return;
    }
    let alive = true;
    renewalDiscountApi
      .get()
      .then((d) => {
        if (alive) setOffer(d?.active ? d : null);
      })
      .catch(() => {
        if (alive) setOffer(null);
      });
    return () => {
      alive = false;
    };
  }, [enabled]);

  return offer;
}
