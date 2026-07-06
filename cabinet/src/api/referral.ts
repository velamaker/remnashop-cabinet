import { api } from "./client";
import type { ReferralProgramResponse } from "@/types/api";

export interface ReferralEarningsResponse {
  earned: number;
  rewards_count: number;
}

export const referralApi = {
  program: () => api.get<ReferralProgramResponse>("/referral/program"),
  earnings: () => api.get<ReferralEarningsResponse>("/referral/earnings"),
};
