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
}

export interface DeviceResponse {
  hwid: string;
  platform: string | null;
  device_model: string | null;
  os_version: string | null;
  user_agent: string | null;
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

export interface SubscriptionOffersResponse {
  gateways: GatewayOfferResponse[];
  plans: PlanOfferResponse[];
  has_current_subscription: boolean;
  current_subscription_status: string | null;
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

export interface PromocodeActivateResponse {
  success: boolean;
  reward_type: string;
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
