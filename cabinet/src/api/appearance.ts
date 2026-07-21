import { api } from "./client";
import { adminApi } from "./admin";
import { ApiError } from "@/types/api";

// Публичное оформление: brand_name всегда конкретный (авто-резолв на бэкенде).
export interface Appearance {
  brand_name: string;
  accent: string | null;
  background: string | null;          // legacy общий фон (фолбэк)
  background_dark: string | null;     // фон тёмной темы
  background_light: string | null;    // фон светлой темы
  support_username: string | null; // username ТП из конфигурации бота (BOT_SUPPORT_USERNAME)
  // URL загруженного логотипа (с cache-busting) или null — тогда дефолтная иконка.
  logo_url?: string | null;
  // Вход через Telegram по OIDC (новый флоу). true — только если на боте заданы
  // TELEGRAM_OIDC_CLIENT_ID/SECRET; иначе кабинет показывает классический виджет.
  telegram_oidc_enabled?: boolean;
  // Показывать прямую ссылку подписки и QR (тумблер админки).
  sub_link_enabled?: boolean;
  // Тех-работы: итоговый флаг (свой тумблер ИЛИ синхрон с ботом) + текст.
  maintenance?: boolean;
  maintenance_message?: string;
  // Что ограничивать в тех-работах (по умолчанию всё — true).
  maintenance_block_login?: boolean;
  maintenance_block_registration?: boolean;
  maintenance_block_payments?: boolean;
  // Доступные языки кабинета: null/пусто = все; иначе список кодов (ru всегда есть).
  enabled_languages?: string[] | null;
}

// Админское: brand_name может быть null (= авто-подхват),
// brand_name_resolved — что покажется, если оставить пусто.
export interface AdminAppearance {
  brand_name: string | null;
  accent: string | null;
  background: string | null;
  background_dark: string | null;
  background_light: string | null;
  brand_name_resolved: string;
  logo_url?: string | null;
  // Тумблеры кабинета (сырые — для страницы оформления в админке).
  sub_link_enabled?: boolean;
  maintenance_enabled?: boolean;
  maintenance_follow_bot?: boolean;
  maintenance_message?: string;
  maintenance_block_login?: boolean;
  maintenance_block_registration?: boolean;
  maintenance_block_payments?: boolean;
  enabled_languages?: string[] | null;
}

// Публичное оформление — доступно без авторизации.
export const appearanceApi = {
  get: () => api.get<Appearance>("/appearance"),
};

// Изменение оформления — только для админов.
export const appearanceAdminApi = {
  get: () => adminApi.get<AdminAppearance>("/appearance"),
  update: (data: {
    brand_name?: string; accent?: string; background?: string;
    background_dark?: string; background_light?: string;
    sub_link_enabled?: boolean; maintenance_enabled?: boolean;
    maintenance_follow_bot?: boolean; maintenance_message?: string;
    maintenance_block_login?: boolean; maintenance_block_registration?: boolean;
    maintenance_block_payments?: boolean; enabled_languages?: string[];
  }) =>
    adminApi.put<AdminAppearance>("/appearance", data),
  // Загрузка логотипа — multipart, поэтому отдельный fetch (adminApi шлёт JSON).
  uploadLogo: async (file: File): Promise<{ logo_url: string | null }> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/admin/appearance/logo", {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) {
      let detail = "Не удалось загрузить логотип";
      try {
        const data = await res.json();
        if (typeof data?.detail === "string") detail = data.detail;
      } catch {
        /* пустой/не-JSON ответ — оставляем дефолтный текст */
      }
      throw new ApiError(res.status, detail);
    }
    return res.json();
  },
  deleteLogo: () => adminApi.delete<{ logo_url: string | null }>("/appearance/logo"),
};
