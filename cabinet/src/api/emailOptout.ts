import { api } from "./client";

/** Подписан ли человек на месячную сводку письмом. Ни почты, ни id в ответе нет. */
export interface EmailOptoutState {
  subscribed: boolean;
}

// Ссылка из письма открывается без входа — и часто на другом устройстве. Повтор
// через refresh-токен тут бессмыслен: доступ даёт подпись в `t`, а не сессия.
const query = (token: string) => `?t=${encodeURIComponent(token)}`;

export const emailOptoutApi = {
  /** Только чтение: открытие ссылки (в том числе сканером почты) ничего не меняет. */
  status: (token: string) =>
    api.get<EmailOptoutState>(`/email-optout/digest${query(token)}`, { skipAuthRetry: true }),
  unsubscribe: (token: string) =>
    api.post<EmailOptoutState>(`/email-optout/digest${query(token)}`, undefined, { skipAuthRetry: true }),
  resubscribe: (token: string) =>
    api.post<EmailOptoutState>(`/email-optout/digest/resubscribe${query(token)}`, undefined, {
      skipAuthRetry: true,
    }),
};
