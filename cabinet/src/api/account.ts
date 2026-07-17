import { api } from "./client";

export const accountApi = {
  // Экспорт всех своих данных одним JSON (профиль, подписка, платежи, входы, тикеты).
  export: () => api.get<Record<string, unknown>>("/account/export"),
  // Самоудаление аккаунта: confirm должен совпасть с фразой подтверждения.
  delete: (confirm: string) => api.post<{ deleted: boolean }>("/account/delete", { confirm }),
};
