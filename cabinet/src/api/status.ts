import { api } from "./client";

export interface UptimeDay {
  date: string; // YYYY-MM-DD
  uptime: number; // % за день
}

export interface StatusNode {
  name: string;
  country_code: string;
  online: boolean;
  host?: string; // адрес ноды — только на авторизованном /subscription/servers (для пинга)
  uptime_30d?: number | null; // аптайм % за 30 дней (из node_health)
  history?: UptimeDay[]; // посуточная история для «свечек»
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
