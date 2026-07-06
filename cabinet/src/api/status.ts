import { api } from "./client";

export interface StatusNode {
  name: string;
  country_code: string;
  online: boolean;
  host: string; // адрес ноды — для клиентского (браузерного) замера пинга
}

export interface StatusResponse {
  nodes: StatusNode[];
  all_operational: boolean;
  total: number;
  online: number;
}

// Публичный статус сервиса (без авторизации).
export const statusApi = {
  get: () => api.get<StatusResponse>("/status"),
};
