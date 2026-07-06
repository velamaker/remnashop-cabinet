import { api } from "./client";
import type {
  DevicesResponse,
  ExtendRequest,
  PaymentInitResponse,
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

  serviceStatus: () =>
    api.get<{
      nodes: { name: string; country_code: string; online: boolean }[];
      all_operational: boolean;
    }>("/subscription/service-status"),

  devices: () => api.get<DevicesResponse>("/subscription/devices"),

  deleteDevice: (hwid: string) =>
    api.delete<{ deleted: boolean }>(
      `/subscription/devices/${encodeURIComponent(hwid)}`,
    ),

  deleteAllDevices: () =>
    api.delete<{ success: boolean }>("/subscription/devices"),

  reissue: () => api.post<{ success: boolean }>("/subscription/reissue"),

  activateTrial: () =>
    api.post<{ success: boolean }>("/subscription/trial"),

  trialInfo: () =>
    api.get<{ available: boolean; days: number; traffic_gb: number; devices: number }>(
      "/subscription/trial-info",
    ),

  offers: () => api.get<SubscriptionOffersResponse>("/subscription/offers"),

  purchase: (data: PurchaseRequest) =>
    api.post<PaymentInitResponse>("/subscription/purchase", data),

  extend: (data: ExtendRequest) =>
    api.post<PaymentInitResponse>("/subscription/extend", data),

  payWithBalance: (data: { plan_code: string; duration_days: number; gateway_type: string }) =>
    api.post<{ success: boolean; purchase_type: string; spent: number; balance: number }>(
      "/subscription/pay-with-balance",
      data,
    ),
};
