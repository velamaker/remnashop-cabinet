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
}

export interface SubscriptionOffersResponse {
  gateways: GatewayOfferResponse[];
  plans: PlanOfferResponse[];
  has_current_subscription: boolean;
  current_subscription_status: string | null;
  // Условия смены тарифа (CHANGE). Шлёт только наш бэкенд. true — остаток переносится
  // по цене дня (сколько — в plan_change_carry); false — перенос выключен, смена
  // начинает срок с нуля. Поля нет (чужой бэкенд, старая сборка) — кабинет не
  // предупреждает и смену тарифа сам не предлагает.
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
