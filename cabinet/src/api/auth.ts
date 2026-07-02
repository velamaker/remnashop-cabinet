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
