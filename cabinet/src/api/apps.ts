import { api } from "./client";
import { adminApi } from "./admin";

// Своё приложение, добавленное админом. deep_link — шаблон с {sub}
// (подставляется ссылка подписки). install_url — ссылка установки (необязательно).
export interface CustomApp {
  id: string;
  name: string;
  desc: string;
  platforms: string[]; // ios|android|windows|macos|androidtv
  deep_link: string;
  install_url: string | null;
}

// Оверрайды ссылок установки, подтянутые из upstream app-config.json:
// { appId(lower): { platform: install_url } }.
export type AppLinkOverrides = Record<string, Record<string, string>>;

// Выбор админа: какое приложение приоритетное и какие показывать.
// priority: id приложения | null;  enabled: список id | null (null = все).
export interface AppsConfig {
  priority: string | null;
  enabled: string[] | null;
  custom: CustomApp[];
  links_source_url?: string | null;
  link_overrides?: AppLinkOverrides;
  links_updated_at?: string | null;
}

export interface RefreshLinksResult {
  ok: boolean;
  count: number;
  updated_at: string;
  apps: string[];
}

export const appsApi = {
  get: () => api.get<AppsConfig>("/apps"),
};

export const appsAdminApi = {
  get: () => adminApi.get<AppsConfig>("/apps"),
  update: (cfg: AppsConfig) => adminApi.put<AppsConfig>("/apps", cfg),
  refreshLinks: (source_url?: string) =>
    adminApi.post<RefreshLinksResult>("/apps/refresh-links", { source_url }),
};
