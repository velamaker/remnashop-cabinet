import { api } from "./client";

export interface PushStatus {
  enabled: boolean;
  devices: number;
}

export interface PushSubscriptionPayload {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

export const pushApi = {
  vapidKey: () => api.get<{ public_key: string }>("/push/vapid-key"),
  status: () => api.get<PushStatus>("/push/status"),
  subscribe: (sub: PushSubscriptionPayload) => api.post<{ ok: boolean }>("/push/subscribe", sub),
  unsubscribe: (endpoint: string) => api.post<{ ok: boolean }>("/push/unsubscribe", { endpoint }),
  test: () => api.post<{ ok: boolean; delivered: number }>("/push/test"),
};
