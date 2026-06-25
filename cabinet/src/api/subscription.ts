import { api } from "./client";
import type {
  DevicesResponse,
  ExtendRequest,
  PaymentInitResponse,
  PromocodeActivateResponse,
  PurchaseRequest,
  SubscriptionInfoResponse,
  SubscriptionOffersResponse,
} from "@/types/api";

export const subscriptionApi = {
  current: () =>
    api.get<SubscriptionInfoResponse | null>("/subscription/current"),

  serverStats: () =>
    api.get<{
      favorite: { name: string; country_code: string; total: number } | null;
      nodes: { name: string; country_code: string; total: number }[];
    }>("/subscription/server-stats"),

  trafficHistory: () =>
    api.get<{ days: { date: string; total: number }[] }>(
      "/subscription/traffic-history",
    ),

  devices: () => api.get<DevicesResponse>("/subscription/devices"),

  deleteDevice: (hwid: string) =>
    api.delete<{ deleted: boolean }>(
      `/subscription/devices/${encodeURIComponent(hwid)}`,
    ),

  deleteAllDevices: () =>
    api.delete<{ success: boolean }>("/subscription/devices"),

  reissue: () => api.post<{ success: boolean }>("/subscription/reissue"),

  activatePromocode: (code: string) =>
    api.post<PromocodeActivateResponse>("/subscription/promocode", { code }),

  activateTrial: () =>
    api.post<{ success: boolean }>("/subscription/trial"),

  offers: () => api.get<SubscriptionOffersResponse>("/subscription/offers"),

  purchase: (data: PurchaseRequest) =>
    api.post<PaymentInitResponse>("/subscription/purchase", data),

  extend: (data: ExtendRequest) =>
    api.post<PaymentInitResponse>("/subscription/extend", data),
};
