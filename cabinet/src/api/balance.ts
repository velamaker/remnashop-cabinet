import { api } from "./client";

export interface BalanceResponse {
  balance: number; // рублёвый кошелёк
  points: number; // баллы рефералки (отдельно)
  point_value_rub: number; // курс: 1 балл = столько ₽ (из настроек кэшбэка)
  total_spent: number;
  total_purchases: number;
  autopay_enabled: boolean;
}

export interface BalanceTransaction {
  payment_id: string;
  status: string;
  gateway_type: string;
  gateway_display_name: string | null;
  purchase_type: string;
  plan_name: string | null;
  original_amount: string;
  discount_percent: number;
  final_amount: string;
  currency: string;
  is_free: boolean;
  is_test: boolean;
  created_at: string | null;
}

export interface TransactionListResponse {
  total: number;
  limit: number;
  offset: number;
  items: BalanceTransaction[];
}

export const balanceApi = {
  get: () => api.get<BalanceResponse>("/balance"),
  spendOnRenewal: (duration_days: number) =>
    api.post<SpendRenewalResponse>("/balance/spend-on-renewal", { duration_days }),
  convertPoints: (points: number) =>
    api.post<ConvertPointsResponse>("/balance/convert-points", { points }),
  setAutopay: (enabled: boolean) =>
    api.post<{ success: boolean; autopay_enabled: boolean }>("/balance/autopay", { enabled }),
  transactions: (params: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    return api.get<TransactionListResponse>(`/balance/transactions?${qs}`);
  },
  topupConfig: () => api.get<TopupConfig>("/balance/topup/config"),
  createTopup: (amount: number, gateway_type: string) =>
    api.post<TopupResponse>("/balance/topup", { amount, gateway_type }),
};

export interface TopupGateway {
  gateway_type: string;
  name: string;
  currency_symbol: string;
}

export interface TopupConfig {
  enabled: boolean;
  bonus_percent: number;
  min_amount: number;
  max_amount: number;
  presets: number[];
  gateways: TopupGateway[];
}

export interface TopupResponse {
  payment_id: string;
  payment_url: string | null;
  amount: string;
  bonus: string;
  total: string;
}

export interface SpendRenewalResponse {
  success: boolean;
  days_added: number;
  spent: number;
  balance: number;
  expire_at: string | null;
}

export const POINT_VALUE_RUB = 7;

export interface ConvertPointsResponse {
  success: boolean;
  converted_points: number;
  credited_rub: number;
  balance: number;
  points: number;
}
