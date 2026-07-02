import { api } from "./client";
import { adminApi } from "./admin";

export type TicketStatus = "open" | "answered" | "closed";

export interface TicketMessage {
  id: number;
  sender: "user" | "admin";
  body: string;
  created_at: string | null;
}

export interface TicketListItem {
  id: number;
  subject: string;
  status: TicketStatus;
  created_at: string | null;
  updated_at: string | null;
  messages_count: number;
}

export interface TicketDetail {
  id: number;
  subject: string;
  status: TicketStatus;
  created_at: string | null;
  updated_at: string | null;
  messages: TicketMessage[];
}

export interface AdminTicketUser {
  id: number;
  name: string | null;
  email: string | null;
  telegram_id: number | null;
}

export interface AdminTicketListItem extends TicketListItem {
  user: AdminTicketUser;
}

export interface AdminTicketDetail extends TicketDetail {
  user: AdminTicketUser;
}

// ---------- Пользователь ----------
export const supportApi = {
  list: () => api.get<{ items: TicketListItem[] }>("/support/tickets"),
  get: (id: number) => api.get<TicketDetail>(`/support/tickets/${id}`),
  create: (subject: string, message: string) =>
    api.post<{ id: number }>("/support/tickets", { subject, message }),
  reply: (id: number, body: string) =>
    api.post<{ success: boolean }>(`/support/tickets/${id}/messages`, { body }),
  close: (id: number) => api.post<{ success: boolean }>(`/support/tickets/${id}/close`),
};

// ---------- Админ ----------
export const supportAdminApi = {
  list: (status?: TicketStatus) =>
    adminApi.get<{ items: AdminTicketListItem[] }>(
      `/support/tickets${status ? `?status=${status}` : ""}`,
    ),
  get: (id: number) => adminApi.get<AdminTicketDetail>(`/support/tickets/${id}`),
  reply: (id: number, body: string) =>
    adminApi.post<{ success: boolean }>(`/support/tickets/${id}/messages`, { body }),
  setStatus: (id: number, status: TicketStatus) =>
    adminApi.post<{ success: boolean }>(`/support/tickets/${id}/status`, { status }),
};
