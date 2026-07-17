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

// Оверрайды ссылок установки, подтянутые резолверами / upstream:
// { appId(lower): { platform: install_url } }.
export type AppLinkOverrides = Record<string, Record<string, string>>;

// Метаданные резолвера по каждой ссылке. degraded=true — ссылка не в родном
// (RU) App Store: приложение снято, отдан стор другой страны (нужен тот Apple ID).
export interface AppLinkMeta {
  source?: string;
  version?: string | null;
  degraded?: boolean;
}
export type AppLinkMetaMap = Record<string, Record<string, AppLinkMeta>>;

// Выбор админа: какое приложение приоритетное и какие показывать.
// priority: id приложения | null;  enabled: список id | null (null = все).
// Ручные оверрайды ссылок админа: { appId(lower): { platform: url } }.
// Побеждают резолвер/upstream — для замены ссылки, когда приложение вернулось в стор.
export type ManualLinks = Record<string, Record<string, string>>;

export interface AppsConfig {
  priority: string | null;
  enabled: string[] | null;
  custom: CustomApp[];
  links_source_url?: string | null;
  link_overrides?: AppLinkOverrides;
  link_meta?: AppLinkMetaMap;
  link_missing?: string[]; // "app:platform" — резолвер есть, рабочей ссылки нет
  manual_links?: ManualLinks;
  links_updated_at?: string | null;
}

export interface RefreshLinksResult {
  ok: boolean;
  count: number;
  updated_at: string;
  apps: string[];
  degraded?: string[];
  missing?: string[];
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
