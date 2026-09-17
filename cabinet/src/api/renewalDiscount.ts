import { api } from "./client";

/** Скидка на продление ДО окончания подписки (см. бэкенд public/renewal_discount.py). */
export interface RenewalDiscountStatus {
  active: boolean;
  percent?: number;
  expires_at?: string | null;
}

export const renewalDiscountApi = {
  // Действующая скидка текущего пользователя. У бэкендов без этой механики —
  // 404/501: кабинет тогда просто не показывает скидку.
  get: () => api.get<RenewalDiscountStatus>("/renewal-discount"),
};
