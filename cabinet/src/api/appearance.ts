import { api } from "./client";
import { adminApi } from "./admin";

// Публичное оформление: brand_name всегда конкретный (авто-резолв на бэкенде).
export interface Appearance {
  brand_name: string;
  accent: string | null;
  background: string | null;
}

// Админское: brand_name может быть null (= авто-подхват),
// brand_name_resolved — что покажется, если оставить пусто.
export interface AdminAppearance {
  brand_name: string | null;
  accent: string | null;
  background: string | null;
  brand_name_resolved: string;
}

// Публичное оформление — доступно без авторизации.
export const appearanceApi = {
  get: () => api.get<Appearance>("/appearance"),
};

// Изменение оформления — только для админов.
export const appearanceAdminApi = {
  get: () => adminApi.get<AdminAppearance>("/appearance"),
  update: (data: { brand_name?: string; accent?: string; background?: string }) =>
    adminApi.put<AdminAppearance>("/appearance", data),
};
