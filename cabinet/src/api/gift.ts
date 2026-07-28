import { api } from "./client";

export interface GiftResult {
  /** balance — код уже в ответе; gateway — код выпустится после оплаты (см. giftApi.my). */
  paid_by: "balance" | "gateway";
  code?: string;
  payment_id?: string;
  payment_url?: string | null;
  plan_name: string;
  duration_days: number;
  price: string;
}

export interface GiftHistoryItem {
  payment_id: string;
  plan_name: string;
  duration_days: number;
  price: string;
  code: string | null;
  issued: boolean;
  created_at: string | null;
}

export const giftApi = {
  create: (plan_code: string, duration_days: number, gateway_type?: string) =>
    api.post<GiftResult>("/gift/create", {
      plan_code,
      duration_days,
      ...(gateway_type ? { gateway_type } : {}),
    }),
  my: () => api.get<{ items: GiftHistoryItem[] }>("/gift/my"),
};
