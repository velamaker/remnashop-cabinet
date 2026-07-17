import { api } from "./client";

export interface TrialDiscountStatus {
  active: boolean;
  percent?: number;
  expires_at?: string | null;
}

export const trialDiscountApi = {
  // Активная скидка на первую покупку для текущего юзера (для баннера-таймера).
  get: () => api.get<TrialDiscountStatus>("/trial-discount"),
};
