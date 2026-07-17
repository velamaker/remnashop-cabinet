import { api } from "./client";

export interface PromoBannerStatus {
  active: boolean;
  title?: string;
  text?: string;
  cta_text?: string;
  cta_url?: string;
  color?: "accent" | "red" | "green" | "amber";
  dismissible?: boolean;
  version?: string;
}

export const promoBannerApi = {
  get: () => api.get<PromoBannerStatus>("/promo-banner"),
};
