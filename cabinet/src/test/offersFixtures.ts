/**
 * Фикстуры витрины, подписки и устройств для тестов апселла и страницы оплаты.
 *
 * Коды тарифов синтетические (SOLO1/DUO2/HOME3/UNL0: цифра — лимит устройств,
 * 0 — без лимита), форма — ровно как в ответах /subscription/*. Даты считаются
 * от «сейчас», иначе тесты начнут врать через месяц.
 */
import type {
  DeviceResponse,
  DevicesResponse,
  PlanOfferResponse,
  SubscriptionInfoResponse,
  SubscriptionOffersResponse,
} from "@/types/api";

export const GW = "YOOKASSA";

export function plan(
  code: string,
  devices: number,
  opts: {
    type?: "NEW" | "CHANGE" | "RENEW";
    traffic?: number;
    /** срок → цена у шлюза GW; пустая строка — срок есть, цены нет */
    prices?: Record<number, string>;
  } = {},
): PlanOfferResponse {
  const prices = opts.prices ?? { 30: String(100 * (devices || 20)), 90: String(270 * (devices || 20)) };
  return {
    id: code.length * 7 + devices,
    public_code: code,
    name: code,
    description: null,
    traffic_limit: opts.traffic ?? 0,
    device_limit: devices,
    type: "BOTH",
    recommended_purchase_type: opts.type ?? "CHANGE",
    durations: Object.entries(prices).map(([days, amount]) => ({
      days: Number(days),
      prices: amount
        ? [
            {
              gateway_type: GW,
              currency: "RUB",
              currency_symbol: "₽",
              original_amount: amount,
              discount_percent: 0,
              final_amount: amount,
              is_free: false,
            },
          ]
        : [],
    })),
  };
}

export function offers(
  plans: PlanOfferResponse[],
  over: Partial<SubscriptionOffersResponse> = {},
): SubscriptionOffersResponse {
  return {
    gateways: [{ gateway_type: GW, currency: "RUB", currency_symbol: "₽" }],
    plans,
    has_current_subscription: true,
    current_subscription_status: "ACTIVE",
    plan_change_keeps_days: false,
    current_days_left: 29,
    current_is_trial: false,
    current_is_unlimited: false,
    current_frozen: false,
    ...over,
  };
}

/** Ответ «Бедолаги» (и старой сборки нашего бэкенда): условий смены нет вовсе. */
export function offersWithoutTerms(plans: PlanOfferResponse[]): SubscriptionOffersResponse {
  return {
    gateways: [{ gateway_type: GW, currency: "RUB", currency_symbol: "₽" }],
    plans,
    has_current_subscription: true,
    current_subscription_status: "ACTIVE",
  };
}

export function sub(over: Partial<SubscriptionInfoResponse> = {}): SubscriptionInfoResponse {
  return {
    user_remna_id: "1",
    status: "ACTIVE",
    is_trial: false,
    traffic_limit: 0,
    device_limit: 2,
    traffic_limit_strategy: "MONTH",
    expire_at: new Date(Date.now() + 29.5 * 86400_000).toISOString(),
    url: "https://example.org/s/1",
    plan_name: "DUO2",
    plan_duration_days: 30,
    used_traffic_bytes: 0,
    lifetime_used_traffic_bytes: null,
    online_at: null,
    ...over,
  };
}

let seq = 0;

export function device(over: Partial<DeviceResponse> = {}): DeviceResponse {
  seq += 1;
  return {
    hwid: `hw${seq}`,
    platform: "Android",
    device_model: `Phone ${seq}`,
    os_version: "14",
    user_agent: "Happ/5.7.0/android/1",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    ...over,
  };
}

/** Одинаковый айфон в двух приложениях — «два слота на один аппарат». */
export function iphoneTwice(): DeviceResponse[] {
  return [
    device({ platform: "ios", device_model: "iPhone 15", user_agent: "Happ/5.7.0/ios/1" }),
    device({ platform: "ios", device_model: "iPhone 15", user_agent: "INCY/2.6.1/ios CFNetwork" }),
  ];
}

export function devicesOf(list: DeviceResponse[], max: number): DevicesResponse {
  return { devices: list, current_count: list.length, max_count: max };
}
