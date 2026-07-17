import { api } from "./client";

export interface GiftResult {
  code: string;
  plan_name: string;
  duration_days: number;
  price: string;
}

export const giftApi = {
  create: (plan_code: string, duration_days: number) =>
    api.post<GiftResult>("/gift/create", { plan_code, duration_days }),
};
