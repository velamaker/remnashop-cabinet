import { api } from "./client";

export interface UserNotification {
  id: number;
  title: string;
  body: string;
  url: string;
  is_read: boolean;
  created_at: string | null;
}

export const notificationsApi = {
  list: (limit = 50) =>
    api.get<{ unread: number; items: UserNotification[] }>(`/notifications?limit=${limit}`),
  unreadCount: () => api.get<{ unread: number }>("/notifications/unread-count"),
  markRead: (id?: number) => api.post<{ ok: boolean }>("/notifications/read", id ? { id } : {}),
  clear: () => api.delete<{ ok: boolean }>("/notifications"),
};
