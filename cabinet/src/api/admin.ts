import { ApiError } from "@/types/api";

const ADMIN_BASE = "/api/admin";

async function adminFetch<T>(path: string, options: Omit<RequestInit, "body"> & { body?: unknown } = {}): Promise<T> {
  const { body, headers, ...rest } = options;
  const init: RequestInit = {
    ...rest,
    credentials: "include",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };

  const res = await fetch(`${ADMIN_BASE}${path}`, init);

  if (!res.ok) {
    let detail = res.statusText || "Unknown error";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export const adminApi = {
  get: <T>(path: string) => adminFetch<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) => adminFetch<T>(path, { method: "POST" as const, body }),
  put: <T>(path: string, body?: unknown) => adminFetch<T>(path, { method: "PUT" as const, body }),
  delete: <T>(path: string) => adminFetch<T>(path, { method: "DELETE" }),
};

// ---------- Types ----------

export interface AdminOverviewResponse {
  users: {
    total: number;
    active: number;
    blocked: number;
    new_today: number;
    new_week: number;
    new_month: number;
    with_active_subscription: number;
    with_expired_subscription: number;
    without_subscription: number;
    with_trial: number;
    paying: number;
  };
  transactions: {
    total: number;
    completed: number;
    gateways: GatewayStats[];
  };
  subscriptions: {
    total: number;
    active: number;
    expired: number;
    disabled: number;
    limited: number;
    trial: number;
    expiring_soon: number;
    unlimited: number;
  };
}

export interface GatewayStats {
  gateway_type: string;
  total_income: number;
  daily_income: number;
  weekly_income: number;
  monthly_income: number;
  last_month_income: number;
  paid_count: number;
  total_transactions: number;
  completed_transactions: number;
  free_transactions: number;
  total_discounts: number;
}

export interface AdminUser {
  // id/telegram_id/email/referral_code приходят null для роли «только просмотр»
  // (сервер маскирует личные идентификаторы).
  id: number | null;
  telegram_id: number | null;
  auth_type: string;
  email: string | null;
  is_email_verified: boolean;
  name: string;
  username: string | null;
  role: number;
  language: string;
  is_blocked: boolean;
  is_bot_blocked: boolean;
  is_trial_available: boolean;
  personal_discount: number;
  purchase_discount: number;
  points: number;
  cabinet_balance?: number;
  referral_code: string | null;
  created_at: string | null;
  last_login_at?: string | null;
}

export interface LoginEvent {
  ip: string | null;
  user_agent: string | null;
  method: string | null;
  created_at: string | null;
}

export interface LoginHistory {
  total: number;
  distinct_ips: number;
  last_login_at: string | null;
  items: LoginEvent[];
}

export interface TrafficByNode {
  available: boolean;
  days: number;
  total: number;
  nodes: { name: string; country_code: string; total: number }[];
}

export interface AdminUserDetail {
  user: AdminUser;
  current_subscription: {
    status: string;
    is_trial: boolean;
    plan_name: string | null;
    expire_at: string | null;
    traffic_limit: number;
    device_limit: number;
  } | null;
  subscriptions_count: number;
  logins?: { total: number; distinct_ips: number; last_login_at: string | null };
  transactions: AdminTransaction[];
}

export interface AdminTransaction {
  // payment_id/user_id приходят null для роли «только просмотр» (маскировка).
  payment_id: string | null;
  user_id: number | null;
  user_name: string | null;
  user_email: string | null;
  status: string;
  gateway_type: string;
  purchase_type: string;
  is_test: boolean;
  amount: string | null; // final_amount из pricing
  currency: string | null; // RUB/USD/XTR
  plan_name: string | null; // что купили (название тарифа)
  plan_duration: number | null; // срок в днях
  created_at: string | null;
  updated_at: string | null;
}

export interface PaginatedResponse<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface AdminPromocode {
  id: number;
  code: string;
  is_active: boolean;
  reward_type: string;
  reward: number | null;
  plan_snapshot?: Record<string, unknown> | null;
  availability: string;
  is_reusable: boolean;
  max_activations: number | null;
  expires_at: string | null;
  created_at: string | null;
  total_activations?: number;
}

// ---------- API calls ----------

export interface SalesPeriod {
  days: number;
  sales_count: number;
  revenue: { currency: string; amount: number }[];
}

export interface SalesStatsResponse {
  periods: SalesPeriod[];
}

export interface DailyStatsPoint {
  date: string;
  registrations: number;
  revenue: Record<string, number>;
}

export interface DailyStatsResponse {
  days: number;
  currencies: string[];
  series: DailyStatsPoint[];
}

export const statisticsApi = {
  overview: () => adminApi.get<AdminOverviewResponse>("/statistics/overview"),
  transactions: () => adminApi.get<unknown>("/statistics/transactions"),
  sales: () => adminApi.get<SalesStatsResponse>("/statistics/sales"),
  daily: (days = 30) =>
    adminApi.get<DailyStatsResponse>(`/statistics/daily?days=${days}`),
};

export const usersAdminApi = {
  list: (params: {
    limit?: number; offset?: number; search?: string; blocked?: boolean;
    role?: number; sort?: string; order?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    if (params.search) qs.set("search", params.search);
    if (params.blocked != null) qs.set("blocked", String(params.blocked));
    if (params.role != null) qs.set("role", String(params.role));
    if (params.sort) qs.set("sort", params.sort);
    if (params.order) qs.set("order", params.order);
    return adminApi.get<PaginatedResponse<AdminUser>>(`/users?${qs}`);
  },
  get: (id: number) => adminApi.get<AdminUserDetail>(`/users/${id}`),
  logins: (id: number) => adminApi.get<LoginHistory>(`/users/${id}/logins`),
  trafficByNode: (id: number, days = 30) =>
    adminApi.get<TrafficByNode>(`/users/${id}/traffic-by-node?days=${days}`),
  block: (id: number, is_blocked: boolean) =>
    adminApi.put<{ success: boolean; is_blocked: boolean }>(`/users/${id}/block`, { is_blocked }),
  setTrial: (id: number, is_trial_available: boolean) =>
    adminApi.put<{ success: boolean; is_trial_available: boolean }>(
      `/users/${id}/trial`,
      { is_trial_available },
    ),
  changeRole: (id: number, role: number) =>
    adminApi.put<{ success: boolean; role: number }>(`/users/${id}/role`, { role }),
  setDiscount: (id: number, personal_discount: number, purchase_discount: number) =>
    adminApi.put<{ success: boolean }>(`/users/${id}/discount`, {
      personal_discount,
      purchase_discount,
    }),
  // Экспорт Excel (.xlsx) с текущими фильтрами: качаем blob (cookie-auth) и скачиваем.
  exportXlsx: async (params: {
    search?: string; blocked?: boolean; role?: number; sort?: string; order?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params.search) qs.set("search", params.search);
    if (params.blocked != null) qs.set("blocked", String(params.blocked));
    if (params.role != null) qs.set("role", String(params.role));
    if (params.sort) qs.set("sort", params.sort);
    if (params.order) qs.set("order", params.order);
    const endpoint = `/api/admin/users/export.xlsx?${qs}`;

    // iOS Safari и встроенные браузеры (Telegram/соцсети) не поддерживают <a download>
    // — открываем файл в новой вкладке (cookie-auth same-origin работает).
    const isIOS =
      /iP(hone|ad|od)/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (isIOS) {
      window.open(endpoint, "_blank");
      return;
    }

    const res = await fetch(endpoint, { method: "GET", credentials: "include" });
    if (!res.ok) throw new Error(`Экспорт не удался (HTTP ${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `users-${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

// ---------- Детект абьюза триала ----------

export interface AbuseAccount {
  id: number;
  name: string;
  email: string | null;
  telegram_id: number | null;
  username: string | null;
  created_at: string | null;
  is_blocked: boolean;
  is_trial_available: boolean;
  trial_used: boolean;
  young_tg: boolean;
}

export interface AbuseCluster {
  signal: "ip" | "hwid" | "email" | "referral";
  key: string;
  severity: "high" | "medium" | "low";
  accounts: AbuseAccount[];
}

export const abuseAdminApi = {
  trials: (params: { min_accounts?: number; only_trial?: boolean } = {}) => {
    const qs = new URLSearchParams();
    if (params.min_accounts != null) qs.set("min_accounts", String(params.min_accounts));
    if (params.only_trial != null) qs.set("only_trial", String(params.only_trial));
    return adminApi.get<{ clusters: AbuseCluster[]; total: number }>(`/abuse/trials?${qs}`);
  },
};

// ---------- Гранулярные права (grants) ----------

export interface GrantSection { key: string; label: string; }
export interface GrantPreset {
  key: string; label: string; full_access: boolean; sections: string[];
}
export interface GrantCatalog { sections: GrantSection[]; presets: GrantPreset[]; }

export interface UserGrant {
  user_id: number;
  role: number;
  has_grant: boolean;
  full_access: boolean;
  can_write: boolean;
  sections: string[];
  expires_at: string | null;
  granted_by: string | null;
  effective: {
    allowed: boolean;
    full_access: boolean;
    can_write: boolean;
    sections: string[];
    source: string;
  };
}

export interface GrantPayload {
  full_access: boolean;
  can_write: boolean;
  sections: string[];
  expires_at: string | null;
}

// ---------- Лента обновлений ----------

export interface UpdateItem {
  version: string;
  name: string;
  date: string | null;
  notes: string;
  url: string | null;
}
export interface UpdatesInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
  repo: string;
  items: UpdateItem[];
}
export const updatesAdminApi = {
  get: () => adminApi.get<UpdatesInfo>("/updates"),
};

export const grantsAdminApi = {
  catalog: () => adminApi.get<GrantCatalog>("/grants/catalog"),
  get: (userId: number) => adminApi.get<UserGrant>(`/grants/${userId}`),
  set: (userId: number, body: GrantPayload) =>
    adminApi.put<UserGrant & { success: boolean }>(`/grants/${userId}`, body),
  remove: (userId: number) =>
    adminApi.delete<{ success: boolean; user_id: number }>(`/grants/${userId}`),
};

export interface AdminTransactionDetail {
  payment_id: string;
  status: string;
  is_test: boolean;
  purchase_type: string;
  gateway_type: string;
  gateway_display_name: string | null;
  payment_method: string | null;
  currency: string;
  pricing: Record<string, unknown> | null;
  plan_snapshot: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
  user: { id: number; name: string | null; email: string | null; username: string | null };
}

export const transactionsAdminApi = {
  list: (params: {
    limit?: number; offset?: number; status?: string; gateway?: string;
    date_from?: string; date_to?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    if (params.status) qs.set("status", params.status);
    if (params.gateway) qs.set("gateway", params.gateway);
    if (params.date_from) qs.set("date_from", params.date_from);
    if (params.date_to) qs.set("date_to", params.date_to);
    return adminApi.get<PaginatedResponse<AdminTransaction>>(`/transactions?${qs}`);
  },
  get: (paymentId: string) => adminApi.get<AdminTransactionDetail>(`/transactions/${paymentId}`),
  // Экспорт Excel (.xlsx) с текущими фильтрами: качаем blob (cookie-auth) и скачиваем.
  exportXlsx: async (params: {
    status?: string; gateway?: string; date_from?: string; date_to?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params.status) qs.set("status", params.status);
    if (params.gateway) qs.set("gateway", params.gateway);
    if (params.date_from) qs.set("date_from", params.date_from);
    if (params.date_to) qs.set("date_to", params.date_to);
    const endpoint = `/api/admin/transactions/export.xlsx?${qs}`;

    // iOS Safari и встроенные браузеры (Telegram/соцсети) не поддерживают <a download>
    // — тап по кнопке молча ничего не качает. Открываем файл в новой вкладке: cookie-auth
    // работает (same-origin), оттуда пользователь сохранит через «Поделиться».
    const isIOS =
      /iP(hone|ad|od)/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (isIOS) {
      window.open(endpoint, "_blank");
      return;
    }

    const res = await fetch(endpoint, {
      method: "GET",
      credentials: "include",
    });
    if (!res.ok) throw new Error(`Экспорт не удался (HTTP ${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `transactions-${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

export const promocodesAdminApi = {
  list: (params: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    return adminApi.get<PaginatedResponse<AdminPromocode>>(`/promocodes?${qs}`);
  },
  create: (data: {
    code: string;
    reward_type: string;
    reward?: number;
    plan_id?: number;
    duration?: number;
    availability?: string;
    is_reusable?: boolean;
    max_activations?: number;
    expires_at?: string;
  }) => adminApi.post<AdminPromocode>("/promocodes", data),
  delete: (id: number) => adminApi.delete<void>(`/promocodes/${id}`),
  toggle: (id: number, is_active: boolean) =>
    adminApi.put<AdminPromocode>(`/promocodes/${id}/toggle`, { is_active }),
  stats: (id: number) => adminApi.get<AdminPromocode & { stats: unknown }>(`/promocodes/${id}/stats`),
};

// ---------- Plans ----------

export interface AdminPlanPrice {
  currency: string;
  price: string;
}

export interface AdminPlanDuration {
  days: number;
  order_index: number;
  prices: AdminPlanPrice[];
}

export interface AdminPlan {
  id: number;
  public_code: string | null;
  name: string;
  description: string | null;
  tag: string | null;
  type: string;
  availability: string;
  traffic_limit_strategy: string;
  traffic_limit: number;
  device_limit: number;
  allowed_telegram_ids: number[];
  allowed_emails: string[];
  internal_squads: string[];
  external_squad: string | null;
  order_index: number;
  is_active: boolean;
  is_trial: boolean;
  durations: AdminPlanDuration[];
  created_at: string | null;
}

export interface AdminSquad {
  uuid: string;
  name: string;
}

export interface AdminSquadsResponse {
  internal: AdminSquad[];
  external: AdminSquad[];
  available: boolean;
}

export const plansAdminApi = {
  list: () => adminApi.get<{ items: AdminPlan[]; total: number }>("/plans"),
  get: (id: number) => adminApi.get<AdminPlan>(`/plans/${id}`),
  squads: () => adminApi.get<AdminSquadsResponse>("/plans/meta/squads"),
  create: (data: Partial<AdminPlan>) => adminApi.post<AdminPlan>("/plans", data),
  update: (id: number, data: Partial<AdminPlan> & { clear_external_squad?: boolean }) =>
    adminApi.put<AdminPlan>(`/plans/${id}`, data),
  toggle: (id: number) => adminApi.put<{ id: number; is_active: boolean }>(`/plans/${id}/toggle`),
  delete: (id: number) => adminApi.delete<void>(`/plans/${id}`),
};

// ---------- Broadcasts ----------

export interface AdminBroadcast {
  task_id: string;
  status: string;
  audience: string;
  total_count: number;
  success_count: number;
  failed_count: number;
  created_at: string | null;
}

export type BroadcastChannel =
  | "TG_ALL" | "TG_SUBSCRIBED" | "TG_UNSUBSCRIBED" | "TG_TRIAL" | "TG_EXPIRED"
  | "EMAIL_ALL" | "EMAIL_SUBSCRIBED" | "EMAIL_TRIAL" | "EMAIL_EXPIRING" | "EMAIL_EXPIRED";

export const broadcastsAdminApi = {
  list: () => adminApi.get<{ items: AdminBroadcast[]; total: number }>("/broadcasts"),
  get: (task_id: string) => adminApi.get<AdminBroadcast>(`/broadcasts/${task_id}`),
  audienceCounts: () => adminApi.get<Record<BroadcastChannel, number>>("/broadcasts/audience-counts"),
  create: (text: string, channels: BroadcastChannel[]) =>
    adminApi.post<{ telegram: string[]; email: number[] }>("/broadcasts", { text, channels }),
};

// ---------- Settings ----------

export interface AdminSettings {
  default_currency: string;
  access: { mode: string; registration_allowed: boolean; payments_allowed: boolean };
  requirements: { rules_required: boolean; channel_required: boolean; rules_link: string; channel_link: string; channel_id: number | null };
  referral: { enable: boolean; level: string; accrual_strategy: string; reward: { type: string; strategy: string; config: Record<string, number> } };
  backup: { enabled: boolean; interval_hours: number; max_files: number; send_to_chat: boolean };
  extra: { device_single_reset: { enabled: boolean; cooldown_hours: number }; device_all_reset: { enabled: boolean; cooldown_hours: number }; link_reset: { enabled: boolean; cooldown_hours: number }; trial_channel_guard: boolean; mini_app_reserve: boolean };
  notifications: Record<string, boolean>;
}

export const settingsAdminApi = {
  get: () => adminApi.get<AdminSettings>("/settings"),
  update: (data: Record<string, unknown>) => adminApi.put<AdminSettings>("/settings", data),
};

// ---------- Cashback (кэшбэк баллами покупателю) ----------

export interface CashbackTier {
  min_days: number;
  percent: number;
}

export interface CashbackConfig {
  enabled: boolean;
  point_value_rub: number;
  tiers: CashbackTier[];
}

export const cashbackAdminApi = {
  get: () => adminApi.get<CashbackConfig>("/cashback"),
  update: (data: Partial<CashbackConfig>) => adminApi.put<CashbackConfig>("/cashback", data),
};

// ---------- Topup (пополнение баланса через шлюзы) ----------

export interface TopupAdminConfig {
  enabled: boolean;
  bonus_percent: number;
  min_amount: number;
  max_amount: number;
  presets: number[];
}

export const topupAdminApi = {
  get: () => adminApi.get<TopupAdminConfig>("/topup"),
  update: (data: Partial<TopupAdminConfig>) => adminApi.put<TopupAdminConfig>("/topup", data),
};

export interface MorningSummaryConfig {
  enabled: boolean;
  hour: number;
  expiring_days: number;
}

export const morningSummaryAdminApi = {
  get: () => adminApi.get<MorningSummaryConfig>("/morning-summary"),
  update: (data: Partial<MorningSummaryConfig>) =>
    adminApi.put<MorningSummaryConfig>("/morning-summary", data),
};

// ---------- Блок «Статус сервиса» в кабинете ----------

export interface ServerStatusConfig {
  enabled: boolean;
  bind_to_subscription: boolean;
  guest_visible: boolean;
}

export const serverStatusAdminApi = {
  get: () => adminApi.get<ServerStatusConfig>("/server-status"),
  update: (data: Partial<ServerStatusConfig>) =>
    adminApi.put<ServerStatusConfig>("/server-status", data),
};

// ---------- Gateways ----------

export interface AdminGateway {
  id: number;
  type: string;
  currency: string;
  is_active: boolean;
  is_configured: boolean;
  order_index: number;
  display_name: string | null;
}

export interface GatewayField {
  name: string;
  secret: boolean;
  is_set: boolean;
  hint: string | null; // последние 4 символа заданного значения (или само значение, если короткое)
}

export const gatewaysAdminApi = {
  list: () => adminApi.get<{ items: AdminGateway[]; total: number }>("/gateways"),
  toggle: (id: number, is_active: boolean) =>
    adminApi.put<{ id: number; is_active: boolean }>(`/gateways/${id}/toggle`, { is_active }),
  fields: (id: number) =>
    adminApi.get<{ fields: GatewayField[] }>(`/gateways/${id}/fields`),
  setField: (id: number, field: string, value: string) =>
    adminApi.put<{ ok: boolean; is_configured: boolean }>(
      `/gateways/${id}/fields/${encodeURIComponent(field)}`,
      { value },
    ),
  test: (id: number) =>
    adminApi.post<{ ok: boolean; payment_id: string; url: string | null; message?: string }>(
      `/gateways/${id}/test`,
      {},
    ),
};

// ---------- Ad Links ----------

export interface AdminAdLink {
  id: number;
  name: string;
  code: string;
  is_active: boolean;
  created_at: string | null;
  stats?: {
    registrations: number;
    trials: number;
    buyers: number;
    trial_buyers: number;
    revenue: Record<string, number>;
    reg_to_buy_rate: number;
    trial_to_buy_rate: number;
  };
}

export const adLinksAdminApi = {
  list: () => adminApi.get<{ items: AdminAdLink[]; total: number }>("/ad-links"),
  stats: (id: number) => adminApi.get<AdminAdLink>(`/ad-links/${id}/stats`),
  create: (data: { name: string; code: string }) => adminApi.post<AdminAdLink>("/ad-links", data),
  update: (id: number, data: { name?: string; is_active?: boolean }) =>
    adminApi.put<AdminAdLink>(`/ad-links/${id}`, data),
  delete: (id: number) => adminApi.delete<void>(`/ad-links/${id}`),
};

// ---------- Subscriptions ----------

export interface AdminSubscription {
  id: number;
  user_id: number;
  status: string;
  is_trial: boolean;
  plan_name: string | null;
  expire_at: string | null;
  traffic_limit: number;
  device_limit: number;
  url: string;
  created_at: string | null;
}

export const subscriptionsAdminApi = {
  getUser: (userId: number) =>
    adminApi.get<{ current: AdminSubscription | null; history: AdminSubscription[] }>(
      `/subscriptions/user/${userId}`
    ),
  extend: (userId: number, days: number) =>
    adminApi.post<{ success: boolean; subscription: AdminSubscription }>(
      `/subscriptions/user/${userId}/extend`, { days }
    ),
  disable: (userId: number) =>
    adminApi.post<{ success: boolean; subscription: AdminSubscription }>(
      `/subscriptions/user/${userId}/disable`
    ),
  delete: (userId: number) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/delete`),
  grant: (userId: number, plan_id: number, days: number, is_trial = false) =>
    adminApi.post<{ success: boolean; subscription: AdminSubscription; action: string }>(
      `/subscriptions/user/${userId}/grant`, { plan_id, days, is_trial }
    ),
  resetTrial: (userId: number) =>
    adminApi.post<{ success: boolean; is_trial_available: boolean }>(
      `/subscriptions/user/${userId}/reset-trial`
    ),
  addPoints: (userId: number, points: number) =>
    adminApi.post<{ success: boolean; points: number }>(
      `/subscriptions/user/${userId}/points`, { points }
    ),
  adjustBalance: (userId: number, amount: number) =>
    adminApi.post<{ success: boolean; cabinet_balance: number }>(
      `/subscriptions/user/${userId}/balance`, { amount }
    ),
};

// ---------- Audit ----------

export interface AuditEntry {
  id: number;
  actor: string;
  method: string;
  path: string;
  status: number;
  created_at: string | null;
}

export const auditAdminApi = {
  list: (limit = 100) =>
    adminApi.get<{ items: AuditEntry[] }>(`/audit?limit=${limit}`),
};

// ---------- Импорт пользователей (как в боте) ----------
export const importAdminApi = {
  status: () => adminApi.get<{ panel: boolean; bot: boolean; xui: boolean }>("/import/status"),
  squads: () => adminApi.get<{ squads: { uuid: string; name: string }[] }>("/import/squads"),
  syncPanel: () => adminApi.post<{ success: boolean; synced: number }>("/import/sync-panel"),
  syncBot: () => adminApi.post<{ success: boolean; synced: number }>("/import/sync-bot"),
  xui: async (file: File, squadUuids: string[]) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("squads", squadUuids.join(","));
    const res = await fetch(`${ADMIN_BASE}/import/xui`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    if (!res.ok) {
      let detail = res.statusText || "Ошибка";
      try {
        const d = await res.json();
        if (typeof d?.detail === "string") detail = d.detail;
      } catch {}
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as { success: boolean; found: number; started: boolean };
  },
};
