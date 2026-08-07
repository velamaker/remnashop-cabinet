import { adminApi } from "./admin";

export type EmailProvider = "gmail" | "yandex" | "mailru" | "brevo" | "custom";

export interface EmailSettings {
  enabled: boolean;
  provider: EmailProvider;
  host: string;
  port: number;
  use_tls: boolean;
  use_ssl: boolean;
  username: string;
  from_email: string;
  from_name: string;
  has_password: boolean;
  has_brevo_key: boolean;
  is_enabled: boolean; // готово ли реально отправлять
  presets: Record<string, { host: string; port: number; use_tls: boolean; use_ssl: boolean }>;
  // Что на подключённом бэкенде применимо (тот же договор, что у «Настроек» и
  // «Входа через Telegram»):
  //  supported — элемента у бэкенда нет вовсе: не рисуем (поверх чужого бота так
  //           гаснут Brevo и тумблер «почта включена» — своего у него нет);
  //  locked — настройка есть и значение НАСТОЯЩЕЕ, но правится не отсюда
  //           (например, задана в .env бота): рисуем выключенной и печатаем
  //           причину рядом;
  //  warnings — поле рабочее, но у него есть цена: поверх «Бедолаги»
  //           сохранённый пароль SMTP ложится в её журнал действий открытым
  //           текстом. Печатаем ВСЕГДА, независимо от locked.
  // Наш собственный бэкенд этих полей не присылает — экран тогда как раньше.
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
  warnings?: Record<string, string>;
}

// Поля для PUT: секреты (password / brevo_api_key) — пустая строка = «не менять».
export interface EmailSettingsUpdate {
  enabled?: boolean;
  provider?: EmailProvider;
  host?: string;
  port?: number;
  use_tls?: boolean;
  use_ssl?: boolean;
  username?: string;
  password?: string;
  from_email?: string;
  from_name?: string;
  brevo_api_key?: string;
}

export const emailSettingsAdminApi = {
  get: () => adminApi.get<EmailSettings>("/email-settings"),
  update: (data: EmailSettingsUpdate) => adminApi.put<EmailSettings>("/email-settings", data),
  sendTest: (to: string) =>
    adminApi.post<{ success: boolean; to: string }>("/email-settings/test", { to }),
};
