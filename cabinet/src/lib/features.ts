/**
 * Возможности бэкенда: что показывать, а что бэкенд не умеет.
 *
 * Кабинет один, а ботов под ним может быть несколько (наш RemnaShop, «Бедолага»,
 * дальше — другие). Вместо кнопок, которые упрутся в ошибку, бэкенд сам сообщает
 * список возможностей в GET /api/appearance.
 *
 * ГЛАВНОЕ ПРАВИЛО. Наш собственный бэкенд поля `features` не отдаёт вовсе — и не
 * должен. Отсутствие поля означает «умеет всё», то есть сегодняшнее поведение
 * один в один. Поэтому проверка написана через `!== false`: она покрывает разом
 * четыре случая — нет оформления, нет поля, нет ключа, null. Писать `=== true`
 * нельзя: на нашей установке это спрятало бы весь кабинет.
 *
 * ИСКЛЮЧЕНИЕ — ключи из FEATURE_NEEDS_BOT (lib/botCapabilities.ts): им нужен код
 * бота новее 1.3.8, и кабинет, обновлённый отдельно от бота, показывает их только
 * по токену из `bot_capabilities`. Список короткий и растёт только с новыми
 * функциями бота — всё остальное по-прежнему «нет ключа = умеет».
 */
import type { Appearance } from "@/api/appearance";
import { FEATURE_NEEDS_BOT, botHas } from "./botCapabilities";

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
  // Очистка ленты уведомлений (DELETE /api/notifications) — отдельная от чтения
  // возможность: ленту бэкенд может отдавать, а удалять всё сразу не уметь.
  | "notifications_clear"
  | "sessions"
  | "info_pages"
  | "apps"
  | "email_verify"
  | "password_change"
  | "account_delete"
  | "data_export"
  | "status_page"
  // «Нужно больше устройств?» — предложение тарифа побольше при заполненном
  // лимите устройств. Адаптер «Бедолаги» выключает: докупка устройств у них своя.
  | "device_upsell"
  // Докупка «+1 устройства» к текущей подписке до конца её срока. Нужен код нового
  // бота (ручки /subscription/extra-device); адаптер «Бедолаги» выключает — там
  // докупка своя, и лимит в их ответах уже с докупленными местами.
  | "extra_device"
  // Докупка «+N ГБ» к текущему окну трафика. Нужен код нового бота (ручки
  // /subscription/extra-traffic); адаптер «Бедолаги» выключает — лимит трафика у
  // них живёт по своим правилам, и наших таблиц там нет.
  | "extra_traffic"
  // «Пользователи» → «Массово по фильтру»: добавить дни подписки и написать
  // отфильтрованным. Наш бэкенд ключей не шлёт — значит, умеет; адаптер «Бедолаги»
  // выключает: поверх чужого бота эти задачи не строим.
  | "bulk_days"
  | "bulk_message"
  | "admin";

/**
 * Доступна ли возможность. Всё неизвестное считается доступным — кроме функций,
 * которым нужен новый бот: их без его токена не показываем (и без оформления тоже).
 */
export function canFeature(
  appearance: Appearance | null | undefined,
  key: FeatureKey,
): boolean {
  // Прежнее правило первым: явный false чужого бэкенда гасит в любом случае.
  if (appearance?.features?.[key] === false) return false;
  const cap = FEATURE_NEEDS_BOT[key];
  return cap ? botHas(appearance, cap) : true;
}
