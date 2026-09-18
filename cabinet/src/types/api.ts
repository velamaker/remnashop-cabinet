// Точное отражение src/web/schemas/* из remnashop backend.

// Бэкенд отдаёт значения строчными ('telegram', 'email', ...).
export type AuthType = "telegram" | "email" | "google" | "yandex" | "vk";

export interface MeResponse {
  telegram_id: number | null;
  auth_type: AuthType;
  email: string | null;
  is_email_verified: boolean;
  pending_email: string | null;
  name: string;
  username: string | null;
  language: string;
  role?: number;
}

export interface AuthResponse {
  expires_at: string;
  refresh_expires_at: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  name?: string;
  referral_code?: string;
  // Ключи документов, с которыми человек согласился на форме. Бэкенд, который
  // требует согласия, без этого списка аккаунт не создаёт вовсе (428).
  accepted_legal_documents?: string[];
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TelegramAuthRequest {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: number;
  hash: string;
}

export interface TelegramWebAppAuthRequest {
  init_data: string;
  // Согласие с документами. Нужно там, где бэкенд его требует: без списка он не
  // создаёт аккаунт вовсе. Телеграмом заходит подавляющее большинство людей,
  // поэтому без этого поля «обязательное согласие» закрывало регистрацию всем.
  accepted_legal_documents?: string[];
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface ChangeEmailRequest {
  email: string;
}

export interface ConfirmEmailVerificationRequest {
  code: string;
}

// ---------- Subscription ----------

export type SubscriptionStatus = "ACTIVE" | "EXPIRED" | "DISABLED" | string;

export interface SubscriptionInfoResponse {
  user_remna_id: string;
  status: SubscriptionStatus;
  is_trial: boolean;
  traffic_limit: number;
  device_limit: number;
  traffic_limit_strategy: string;
  expire_at: string;
  url: string;
  plan_name: string;
  plan_duration_days: number;
  used_traffic_bytes: number | null;
  lifetime_used_traffic_bytes: number | null;
  online_at: string | null;
  // Пауза, поставленная самим пользователем. В панели подписка при этом
  // выключена (status DISABLED), и без этого признака кабинет показывал
  // «истекла, продлите» человеку, который сам нажал «Пауза». Бэкенды, которые
  // паузы не умеют, поля не присылают — считаем, что паузы нет.
  frozen?: boolean;
}

export interface DeviceResponse {
  hwid: string;
  platform: string | null;
  device_model: string | null;
  os_version: string | null;
  user_agent: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DevicesResponse {
  devices: DeviceResponse[];
  current_count: number;
  max_count: number;
}

export type PaymentGatewayType =
  | "YOOKASSA"
  | "TELEGRAM_STARS"
  | "CRYPTOMUS"
  | string;

export interface GatewayOfferResponse {
  gateway_type: PaymentGatewayType;
  currency: string;
  currency_symbol: string;
}

export interface DurationGatewayPriceResponse {
  gateway_type: PaymentGatewayType;
  currency: string;
  currency_symbol: string;
  original_amount: string;
  discount_percent: number;
  final_amount: string;
  is_free: boolean;
}

export interface DurationOfferResponse {
  days: number;
  prices: DurationGatewayPriceResponse[];
}

export interface PlanOfferResponse {
  id: number;
  public_code: string;
  name: string;
  description: string | null;
  traffic_limit: number;
  device_limit: number;
  type: string;
  recommended_purchase_type: "NEW" | "CHANGE" | "RENEW" | string;
  durations: DurationOfferResponse[];
}

/**
 * Сколько дней добавит перенос остатка при смене на (тариф, срок, валюта).
 * `mode`: carry — по цене дня; same_plan — тот же тариф, 1:1; unpriced — у срока нет
 * цены, перенести нельзя; none — новый тариф бессрочный, переносить нечего.
 * `lost_days` уже включает упор в технический предел (`capped`).
 */
export interface PlanChangeCarryEntry {
  plan_code: string;
  duration_days: number;
  currency: string;
  mode: "carry" | "same_plan" | "unpriced" | "none" | string;
  bonus_days: number;
  lost_days: number;
  capped?: boolean;
  extras_lost?: number;
  // Почему часть дней не перенесётся: cap — упор в предел переноса; old_price — цена
  // прежних дней неизвестна; new_price — у срока нет цены.
  lost_reason?: "cap" | "old_price" | "new_price" | string | null;
}

export interface SubscriptionOffersResponse {
  gateways: GatewayOfferResponse[];
  plans: PlanOfferResponse[];
  has_current_subscription: boolean;
  current_subscription_status: string | null;
  // Условия смены тарифа (CHANGE). Шлёт только наш бэкенд. Флаг для СТАРЫХ сборок:
  // true — ничего не пропадёт (они не предупреждают); false — что-то сгорит или перенос
  // выключен. Поля нет (чужой бэкенд) — кабинет не предупреждает и смену сам не предлагает.
  plan_change_keeps_days?: boolean;
  // Полные оставшиеся сутки (на паузе — сохранённый остаток); null у бессрочной.
  current_days_left?: number | null;
  current_is_trial?: boolean | null;
  current_is_unlimited?: boolean | null;
  // null — бэкенд не смог узнать про паузу.
  current_frozen?: boolean | null;
  // Режим текущей подписки при переносе: carry | lifetime | reserve | refund | none.
  carry_mode?: string | null;
  // Записи только при carry_mode = carry и только для тарифов со сменой.
  plan_change_carry?: PlanChangeCarryEntry[] | null;
  // Перенос включён и условия посчитаны — текущий кабинет берёт их из plan_change_carry.
  plan_change_carry_active?: boolean | null;
  // Докупленные места под устройства у ТЕКУЩЕЙ подписки. Полей нет (старый бот или
  // чужой бэкенд) — кабинет про них молчит: обещать перенос стоимости, не зная,
  // считает ли её бэкенд, нельзя.
  current_device_limit?: number | null;
  current_extra_devices?: number | null;
  current_extra_until?: string | null;
  /** Докупленный трафик текущего окна: сколько ГБ и до когда. null — «не знаем». */
  current_extra_traffic_gb?: number | null;
  current_extra_traffic_until?: string | null;
}

// ---------- Докупка +1 устройства ----------

/** Цена одного действия: сумма целыми рублями, до какой даты и сколько это дней. */
export interface ExtraDevicePriceResponse {
  amount: string;
  until: string;
  days: number;
}

export interface ExtraDeviceSlotResponse {
  slot_id: number;
  ends_at: string;
  /** null — продлевать нечего (подписка кончается вместе с местом). */
  extend: ExtraDevicePriceResponse | null;
}

/**
 * Предложение докупки. `enabled: false` — продажи закрыты, и цену бэкенд не
 * раскрывает вовсе, поэтому остальные поля необязательные.
 */
export interface ExtraDeviceResponse {
  enabled: boolean;
  currency?: string;
  currency_symbol?: string;
  price_per_30d?: string | null;
  max_extra?: number;
  device_limit?: number;
  plan_device_limit?: number;
  extra_count?: number;
  subscription_expire_at?: string | null;
  balance?: string;
  /** Отключатся ли устройства, подключённые после покупки, когда место кончится. */
  removes_excess?: boolean;
  gateways?: { gateway_type: string; currency_symbol: string }[];
  new?: {
    available: boolean;
    /** Код причины: max_reached | already_used | too_late | trial | … */
    reason?: string | null;
    amount?: string;
    until?: string;
    days?: number;
  };
  slots?: ExtraDeviceSlotResponse[];
}

export interface ExtraDeviceBuyRequest {
  request_id: string;
  kind: "new" | "extend";
  slot_id?: number;
  pay: "balance" | "gateway";
  gateway_type?: string;
  expected_amount: string;
}

/**
 * Итог покупки. Бизнес-отказы приходят как 200 с `result`, а не как ошибка HTTP:
 * `ApiError.detail` — строка, и перевести код причины из неё было бы нечем.
 */
export interface ExtraDeviceBuyResponse {
  result: "applied" | "pending" | "price_changed" | "insufficient_balance" | "not_available";
  device_limit?: number | null;
  until?: string | null;
  spent?: string;
  balance?: string | null;
  payment_id?: string;
  payment_url?: string;
  amount?: string;
  need?: string;
  reason?: string;
  quote?: ExtraDeviceResponse;
  repeat?: boolean;
}

/**
 * Предложение докупить трафик. `enabled: false` — продажи закрыты, и цену бэкенд не
 * раскрывает вовсе, поэтому остальные поля необязательные.
 */
export interface ExtraTrafficResponse {
  enabled: boolean;
  currency?: string;
  currency_symbol?: string;
  /** Сколько ГБ даёт одна покупка. */
  gb?: number;
  /** Цена за покупку, целые рубли строкой. */
  price?: string | null;
  balance?: string;
  /** Текущий лимит и лимит тарифа — в ГБ, как их видит панель. */
  traffic_limit_gb?: number;
  plan_traffic_limit_gb?: number;
  /** Расход из панели. null — панель не ответила. */
  used_bytes?: number | null;
  /** Уже докуплено в этом периоде, ГБ. */
  extra_gb_active?: number;
  strategy?: string;
  /** Когда панель обнулит расход. null — стратегия без обнуления. */
  resets_at?: string | null;
  /** С какого процента расхода показывать предложение на Главной. */
  show_from_percent?: number;
  subscription_expire_at?: string | null;
  gateways?: { gateway_type: string; currency_symbol: string }[];
  offer?: {
    available: boolean;
    /** Код причины: trial | unlimited_traffic | reset_too_soon | panel_unavailable | … */
    reason?: string | null;
    until?: string | null;
    hours_left?: number | null;
  };
}

export interface ExtraTrafficBuyRequest {
  request_id: string;
  pay: "balance" | "gateway";
  gateway_type?: string;
  expected_amount: string;
  expected_gb: number;
}

/**
 * Итог покупки. Бизнес-отказы приходят как 200 с `result`, а не как ошибка HTTP:
 * `ApiError.detail` — строка, и перевести код причины из неё было бы нечем.
 */
export interface ExtraTrafficBuyResponse {
  result: "applied" | "pending" | "price_changed" | "insufficient_balance" | "not_available";
  gb?: number;
  traffic_limit_gb?: number | null;
  /** Панель сама сняла LIMITED — доступ вернулся вместе с трафиком. */
  unlocked?: boolean;
  until?: string | null;
  spent?: string;
  balance?: string | null;
  payment_id?: string;
  payment_url?: string;
  amount?: string;
  need?: string;
  reason?: string;
  quote?: ExtraTrafficResponse;
  repeat?: boolean;
}

export interface PurchaseRequest {
  plan_code: string;
  duration_days: number;
  gateway_type: PaymentGatewayType;
}

export interface ExtendRequest {
  duration_days: number;
  gateway_type: PaymentGatewayType;
}

export interface PaymentInitResponse {
  payment_id: string;
  payment_url: string | null;
  purchase_type: string;
  status: string;
  is_free: boolean;
  final_amount: string;
  currency: string;
}

// ---------- Public plans (landing, no auth) ----------

export interface PublicPlanLandingResponse {
  public_code: string;
  name: string;
  description: string | null;
  traffic_limit: number;
  device_limit: number;
  monthly_from_rub: string;
  max_duration_days: number;
  max_duration_price_rub: string;
}

export interface PublicPlanLandingListResponse {
  plans: PublicPlanLandingResponse[];
}

// ---------- Referral ----------

export interface ReferralRewardLevelResponse {
  level: number;
  value: number;
  // Готовая подпись награды с бэкенда. Наш бот её не шлёт: у него ступень — это
  // число плюс общая единица измерения (`value` + «% от платежей»). У «Бедолаги»
  // одна ступень платит процентом, фиксированной суммой и днями конкретного
  // тарифа сразу — такое числом не передать, поэтому строку собирает бэкенд, а
  // кабинет печатает её как есть.
  label?: string;
}

export interface ReferralProgramResponse {
  enabled: boolean;
  referral_code: string;
  invited_count: number;
  invited_with_payment_count: number;
  reward_type: string;
  reward_strategy: string;
  accrual_strategy: string;
  max_level: number;
  reward_levels: ReferralRewardLevelResponse[];
}

// ---------- API error shape (FastAPI HTTPException) ----------

export interface ApiErrorBody {
  detail: string;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}
