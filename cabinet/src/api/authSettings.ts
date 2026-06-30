import { adminApi } from "./admin";

export interface AuthSettings {
  telegram_oidc_client_id: string;
  has_secret: boolean;
  // Сохранённый тумблер: null (авто) | true | false.
  telegram_oidc_enabled_setting: boolean | null;
  // Эффективно ли OIDC сейчас работает (тумблер + наличие кредов).
  telegram_oidc_active: boolean;
  // Готовый Redirect URI для @BotFather → Web Login.
  redirect_uri: string;
}

// Поля для PUT: секрет (client_secret) — пустая строка = «не менять».
export interface AuthSettingsUpdate {
  telegram_oidc_enabled?: boolean;
  telegram_oidc_client_id?: string;
  telegram_oidc_client_secret?: string;
}

export const authSettingsAdminApi = {
  get: () => adminApi.get<AuthSettings>("/auth-settings"),
  update: (data: AuthSettingsUpdate) => adminApi.put<AuthSettings>("/auth-settings", data),
};
