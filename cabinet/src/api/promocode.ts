import { api } from "./client";

export interface PromocodeActivateResponse {
  success: boolean;
  code: string;
  reward_type: string;
  reward: number | null;
}

export const promocodeApi = {
  activate: (code: string) =>
    api.post<PromocodeActivateResponse>("/promocode/activate", { code }),
};
