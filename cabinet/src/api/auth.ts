import { api } from "./client";
import type {
  AuthResponse,
  ChangeEmailRequest,
  ChangePasswordRequest,
  ConfirmEmailVerificationRequest,
  LoginRequest,
  MeResponse,
  RegisterRequest,
  TelegramAuthRequest,
  TelegramWebAppAuthRequest,
} from "@/types/api";

export const authApi = {
  me: () => api.get<MeResponse>("/auth/me"),
  whoami: () =>
    api.get<{
      role: number | null;
      is_admin: boolean;
      is_readonly_admin: boolean;
      can_access_admin: boolean;
      is_owner: boolean;
      full_access: boolean;
      can_write: boolean;
      sections: string[];
      /** Необязательный список доступных СТРАНИЦ админки (адреса вида "/admin/users").
       *  Нужен бэкендам, которые умеют лишь часть разделов: секция «Настройки» у нас
       *  одна на два десятка страниц, и без этого списка отсутствие одной ручки
       *  прячет весь блок целиком. Поле не пришло — доступны все страницы (так
       *  отвечает наш бэкенд, поведение прежних установок не меняется). */
      pages?: string[];
      /** Страницы, которые бэкенд отдаёт только на ЧТЕНИЕ (те же адреса).
       *  Раздел показать можно, а кнопку «Сохранить» — нет: у чужого бота часть
       *  настроек живёт в его собственных экранах. Поля нет — сохранять можно
       *  везде (так отвечает наш бэкенд). */
      readonly_pages?: string[];
      /** Почему той или иной страницы админки здесь нет: адрес → человеческая причина.
       *  Пункт меню скрыт (см. pages), но адрес остаётся рабочим — по нему приходят
       *  из закладок и по памяти, и пустой экран без слов читается как поломка.
       *  Поле необязательное: наш бэкенд его не шлёт, тогда причины просто нет. */
      page_notes?: Record<string, string>;
      grant_expires_at: string | null;
      has_password: boolean;
    }>("/auth/whoami"),
  setPassword: (password: string) =>
    api.post<{ success: boolean; has_password: boolean }>("/auth/password/set", { password }),

  login: (data: LoginRequest) =>
    api.post<AuthResponse>("/auth/login", data, { skipAuthRetry: true }),

  register: (data: RegisterRequest) =>
    api.post<AuthResponse>("/auth/register", data, { skipAuthRetry: true }),

  logout: () => api.post<{ success: boolean }>("/auth/logout"),

  telegramLogin: (data: TelegramAuthRequest) =>
    api.post<AuthResponse>("/auth/telegram", data, { skipAuthRetry: true }),

  telegramWebAppLogin: (data: TelegramWebAppAuthRequest) =>
    api.post<AuthResponse>("/auth/telegram/webapp", data, { skipAuthRetry: true }),

  linkTelegram: (data: TelegramAuthRequest) =>
    api.post<MeResponse>("/auth/telegram/link", data),

  changePassword: (data: ChangePasswordRequest) =>
    api.post<{ success: boolean }>("/auth/change-password", data),

  changeEmail: (data: ChangeEmailRequest) =>
    api.post<{ success: boolean; pending_email: string }>(
      "/auth/email/change",
      data,
    ),

  requestEmailVerification: (email?: string) =>
    api.post<{ success: boolean; target_email: string; expires_at: string }>(
      "/auth/email/request-verification",
      { email },
    ),

  confirmEmailVerification: (data: ConfirmEmailVerificationRequest) =>
    api.post<{ success: boolean; email: string }>("/auth/email/confirm", data),

  // Удалить привязанную почту (доступно только при наличии Telegram-входа).
  deleteEmail: () => api.delete<{ success: boolean }>("/auth/email"),

  // Сброс пароля по email («Забыл пароль») — для НЕвошедших.
  // Ответ на /request всегда success (не раскрываем, есть ли аккаунт).
  resetPasswordRequest: (email: string) =>
    api.post<{ success: boolean }>("/auth/password/reset/request", { email }, { skipAuthRetry: true }),

  resetPasswordConfirm: (data: { email: string; code: string; password: string }) =>
    api.post<{ success: boolean }>("/auth/password/reset/confirm", data, { skipAuthRetry: true }),
};
