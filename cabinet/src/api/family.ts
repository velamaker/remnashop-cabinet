import { api } from "./client";

/**
 * Семейные профили: N профилей к семейному тарифу владельца, у каждого своя ссылка
 * подписки и свои устройства. Участнику аккаунт не нужен — ссылку пересылает
 * владелец. Бизнес-отказы приходят 200 с полем `result` (как у докупки устройства):
 * `ApiError.detail` — строка, и коду причины неоткуда взять перевод.
 */

export type FamilyProfileStatus = "creating" | "active" | "suspended" | "deleting";

export interface FamilyProfile {
  id: number;
  label: string;
  status: FamilyProfileStatus;
  /** plan | owner_expired | owner_frozen | owner_gone | owner_blocked | panel_missing */
  suspend_reason: string | null;
  expired: boolean;
  expire_at: string | null;
  /** Ссылка подписки профиля — только у работающего. */
  url: string | null;
  device_limit: number;
  /** null — панель не ответила (или запрос был «лёгким»). */
  devices: number | null;
  /** 0 — без ограничений. */
  traffic_limit_bytes: number | null;
  traffic_used_bytes: number | null;
  created_at: string | null;
  device_reset_at?: string | null;
}

export interface FamilyResponse {
  enabled: boolean;
  /** Можно ли прямо сейчас завести ещё один профиль. */
  available: boolean;
  /** Почему нельзя: disabled | no_subscription | blocked | trial | not_family |
   *  frozen | reserve | not_active | max_reached | period_limit. */
  reason?: string | null;
  plan_name?: string | null;
  terms?: { max_profiles: number; devices_per_profile: number } | null;
  used?: number;
  /** Сколько профилей заведено за оплаченный период (включая удалённые) и сколько
   *  можно: места тарифа + одна замена. Нет полей — бот старее этого правила. */
  created_in_period?: number;
  period_limit?: number | null;
  profiles: FamilyProfile[];
  reset_devices?: { enabled: boolean; cooldown_hours: number };
}

export type FamilyCreateResult =
  | { result: "created"; profile_id: number; repeat?: boolean }
  | { result: "pending"; profile_id?: number }
  | { result: "deleted" }
  | { result: "failed" }
  | { result: "label_taken" }
  | { result: "bad_label" }
  | { result: "bad_request" }
  | { result: "not_available"; reason: string }
  /** Очередь семьи занята (крон сверяет её с панелью) — повторить через минуту. */
  | { result: "busy" };

export type FamilyResetResult =
  | { result: "reset" }
  | { result: "cooldown"; available_at: string | null }
  | { result: "not_available"; reason: string }
  | { result: "busy" };

export const familyApi = {
  get: () => api.get<FamilyResponse>("/family"),
  /** Без походов в панель: только чтобы решить, показывать ли пункт меню. */
  summary: () => api.get<FamilyResponse>("/family?light=1"),
  create: (data: { request_id: string; label: string }) =>
    api.post<FamilyCreateResult>("/family/profiles", data),
  resetDevices: (id: number) =>
    api.post<FamilyResetResult>(`/family/profiles/${id}/reset-devices`, {}),
  remove: (id: number) =>
    api.delete<{ result: "deleted" | "pending" | "busy" }>(`/family/profiles/${id}`),
};

/** Показывать ли вход «Семья» — то же правило, что у бота (menu_visible): профили есть —
 *  виден всегда (даже при выключенной функции: ссылка и «Удалить»); профилей нет —
 *  только при включённой функции и семейном тарифе. */
export function familyVisible(data: FamilyResponse | null | undefined): boolean {
  if (!data) return false;
  if ((data.profiles ?? []).length > 0) return true;
  return Boolean(data.enabled && data.terms);
}
