import { api } from "./client";
import type { ReferralProgramResponse } from "@/types/api";

export interface ReferralEarningsResponse {
  earned: number;
  rewards_count: number;
  // Дни подписки, начисленные за приглашения. Наш бэкенд поля не шлёт (у него
  // награда одного вида, и она лежит в `earned`), а у «Бедолаги» ступень платит
  // деньгами и днями одновременно — см. lib/referralReward.ts.
  earned_days?: number;
}

export const referralApi = {
  program: () => api.get<ReferralProgramResponse>("/referral/program"),
  earnings: () => api.get<ReferralEarningsResponse>("/referral/earnings"),
  // Досчёт приглашения после входа через Telegram: у того входа поля под реф-код
  // нет вовсе, поэтому код едет отдельным шагом. Сервер сам решает, засчитывать
  // ли его (новизна аккаунта, чужой ли код, нет ли уже пригласившего).
  attach: (code: string) => api.post<{ success: boolean }>("/referral/attach", { code }),
};
