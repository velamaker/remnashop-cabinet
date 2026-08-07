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
  // Что на подключённом бэкенде применимо (тот же договор, что у «Настроек»):
  //  supported — поля нет вовсе: не рисуем;
  //  locked — поле есть и значение настоящее, но правится не отсюда: рисуем
  //           выключенным и печатаем причину.
  //  warnings — поле рабочее, но у него есть цена (например, поверх чужого бота
  //           сохранённый секрет попадает в его журнал открытым текстом):
  //           печатаем предупреждение рядом с полем ВСЕГДА, независимо от locked.
  // Наш собственный бэкенд этих полей не присылает — экран тогда как раньше.
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
  warnings?: Record<string, string>;
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
