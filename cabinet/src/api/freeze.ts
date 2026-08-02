import { api } from "./client";

export interface FreezeStatus {
  enabled: boolean;
  frozen: boolean;
  can_freeze?: boolean;
  remaining_days?: number;
  max_days?: number;
  days_left?: number;
  /** Минимальный срок паузы в днях: раньше него бэкенд снять её не даст (у бэкендов,
   *  которые продлевают подписку целыми сутками, вернуть меньше дня просто нечем).
   *  Поле необязательное — наш бэкенд его не отдаёт, и тогда ограничения нет. */
  min_days?: number;
}

export const freezeApi = {
  status: () => api.get<FreezeStatus>("/subscription/freeze-status"),
  freeze: () => api.post<{ frozen: boolean; remaining_days: number }>("/subscription/freeze", {}),
  unfreeze: () => api.post<{ frozen: boolean }>("/subscription/unfreeze", {}),
};
