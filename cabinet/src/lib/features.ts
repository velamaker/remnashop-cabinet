/**
 * Возможности бэкенда: что показывать, а что бэкенд не умеет.
 *
 * Кабинет один, а ботов под ним может быть несколько (наш RemnaShop, «Бедолага»,
 * дальше — другие). Вместо кнопок, которые упрутся в ошибку, бэкенд сам сообщает
 * список возможностей в GET /api/appearance.
 *
 * ГЛАВНОЕ ПРАВИЛО. Наш собственный бэкенд поля `features` не отдаёт вовсе — и не
 * должен. Отсутствие поля означает «умеет всё», то есть сегодняшнее поведение
 * один в один. Поэтому проверка ровно одна и написана через `!== false`: она
 * покрывает разом четыре случая — нет оформления, нет поля, нет ключа, null.
 * Писать `=== true` нельзя: на нашей установке это спрятало бы весь кабинет.
 */
import type { Appearance } from "@/api/appearance";

export type FeatureKey =
  | "subscription"
  | "trial"
  | "devices"
  | "servers"
  | "service_status"
  | "traffic_history"
  | "server_stats"
  | "freeze"
  | "reissue"
  | "purchase"
  | "plans_public"
  | "pay_with_balance"
  | "topup"
  | "autopay"
  | "points"
  | "transactions"
  | "renew_from_balance"
  | "promocode"
  | "referral"
  | "gift"
  | "tickets"
  | "push"
  | "notifications"
  | "sessions"
  | "info_pages"
  | "apps"
  | "email_verify"
  | "password_change"
  | "account_delete"
  | "data_export"
  | "status_page"
  | "admin";

/** Доступна ли возможность. Всё неизвестное считается доступным. */
export function canFeature(
  appearance: Appearance | null | undefined,
  key: FeatureKey,
): boolean {
  return appearance?.features?.[key] !== false;
}
