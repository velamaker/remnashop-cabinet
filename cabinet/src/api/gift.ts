import { api } from "./client";

export interface GiftResult {
  /** balance — код уже в ответе; gateway — код выпустится после оплаты (см. giftApi.my). */
  paid_by: "balance" | "gateway";
  code?: string;
  /** Страница сертификата — её пересылают получателю вместо голого кода. */
  certificate_url?: string;
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
  /** Ссылка на сертификат; есть только у выпущенных подарков. */
  certificate_url?: string | null;
  created_at: string | null;
}

/** Что видно на открытке. Кто подарил — не отдаётся: ссылку пересылают. */
export interface GiftCertificate {
  code: string;
  plan_name: string;
  days: number;
  /** ready — можно активировать; activated — уже забрали; void — код удалён. */
  state: "ready" | "activated" | "void";
  /** Активация в боте одним нажатием; null — бот сейчас не узнать. */
  bot_url: string | null;
}

export const giftApi = {
  create: (plan_code: string, duration_days: number, gateway_type?: string) =>
    api.post<GiftResult>("/gift/create", {
      plan_code,
      duration_days,
      ...(gateway_type ? { gateway_type } : {}),
    }),
  my: () => api.get<{ items: GiftHistoryItem[] }>("/gift/my"),
  certificate: (code: string) =>
    api.get<GiftCertificate>(`/gift/certificate/${encodeURIComponent(code)}`),
};
