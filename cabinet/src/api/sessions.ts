import { api } from "./client";

export interface SessionEntry {
  ip: string | null;
  user_agent: string | null;
  method: string | null;
  created_at: string | null;
}

export const sessionsApi = {
  list: () => api.get<{ items: SessionEntry[] }>("/sessions"),
  logoutAll: () => api.post<{ ok: boolean }>("/sessions/logout-all", {}),
};
