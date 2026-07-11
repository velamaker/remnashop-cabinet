import { api } from "./client";

export interface StatusNode {
  name: string;
  country_code: string;
  online: boolean;
  host?: string; // адрес ноды — только на авторизованном /subscription/servers (для пинга)
}

export interface StatusResponse {
  nodes: StatusNode[];
  all_operational: boolean;
  total: number;
  online: number;
  enabled?: boolean; // отдаёт /subscription/servers: включён ли блок в админке
}

// Публичный статус сервиса (без авторизации) — БЕЗ host (без пинга, приватность).
export const statusApi = {
  get: () => api.get<StatusResponse>("/status"),
  // Серверы вошедшего пользователя (привязка по подписке) — с host для пинга.
  myServers: () => api.get<StatusResponse>("/subscription/servers"),
};
