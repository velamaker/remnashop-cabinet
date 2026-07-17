import { api } from "./client";

export interface FreezeStatus {
  enabled: boolean;
  frozen: boolean;
  can_freeze?: boolean;
  remaining_days?: number;
  max_days?: number;
  days_left?: number;
}

export const freezeApi = {
  status: () => api.get<FreezeStatus>("/subscription/freeze-status"),
  freeze: () => api.post<{ frozen: boolean; remaining_days: number }>("/subscription/freeze", {}),
  unfreeze: () => api.post<{ frozen: boolean }>("/subscription/unfreeze", {}),
};
