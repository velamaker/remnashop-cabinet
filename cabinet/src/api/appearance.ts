import { api } from "./client";
import { adminApi } from "./admin";
import { ApiError } from "@/types/api";

// Публичное оформление: brand_name всегда конкретный (авто-резолв на бэкенде).
export interface Appearance {
  brand_name: string;
  accent: string | null;
  background: string | null;
  support_username: string | null; // username ТП из конфигурации бота (BOT_SUPPORT_USERNAME)
  // URL загруженного логотипа (с cache-busting) или null — тогда дефолтная иконка.
  logo_url?: string | null;
  // Вход через Telegram по OIDC (новый флоу). true — только если на боте заданы
  // TELEGRAM_OIDC_CLIENT_ID/SECRET; иначе кабинет показывает классический виджет.
  telegram_oidc_enabled?: boolean;
}

// Админское: brand_name может быть null (= авто-подхват),
// brand_name_resolved — что покажется, если оставить пусто.
export interface AdminAppearance {
  brand_name: string | null;
  accent: string | null;
  background: string | null;
  brand_name_resolved: string;
  logo_url?: string | null;
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
