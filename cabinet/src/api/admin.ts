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
    } catch {
      /* тело ответа не JSON — оставляем дефолтный текст ошибки */
    }
    // 2FA админа: требуется разблокировка — сигналим глобально, модалка перехватит.
    if (res.status === 403 && detail === "2fa_required") {
      window.dispatchEvent(new CustomEvent("admin-2fa-required"));
    }
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
  expire_at?: string | null; // только при фильтре «истекают»
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

export interface ReferralMember {
  id: number;
  name: string;
  username: string | null;
  created_at: string | null;
}

export interface UserReferrals {
  referrer: ReferralMember | null;
  referrals: ReferralMember[];
  second_level: ReferralMember[];
  counts: { first: number; second: number };
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
  cohorts: (months = 12) =>
    adminApi.get<CohortsResponse>(`/statistics/cohorts?months=${months}`),
  metrics: () => adminApi.get<MetricsResponse>("/statistics/metrics"),
};

export interface MetricsTopPlan {
  name: string;
  revenue: number;
  count: number;
}
export interface MetricsTopGateway {
  gateway_type: string;
  revenue: number;
  count: number;
}
export interface MetricsRefundsCurrency {
  currency: string;
  count: number;
  amount: number;
}
/** Возвраты за 30 дней по дате возврата; валюты не складываются. */
export interface MetricsRefunds {
  count_30d: number;
  by_currency: MetricsRefundsCurrency[];
  /** Активные шлюзы, которые сообщают об отзыве платежа (ставят REFUNDED). */
  reporting_gateways: string[];
  /** Активные шлюзы, которые о возвратах молчат: их возвратов бот не видит. */
  silent_gateways: string[];
}
export interface MetricsResponse {
  currency: string;
  mrr: number;
  mrr_subs: number;
  arpu: number;
  arppu: number;
  revenue_30d: number;
  active_users: number;
  payers_30d: number;
  conversion: { trials: number; converted: number; pct: number };
  churn: { active_now: number; churned_30d: number; pct: number };
  payments: { completed_30d: number; canceled_30d: number; success_pct: number };
  /** Нет поля — бэкенд возвраты не считает, плитку не рисуем. */
  refunds?: MetricsRefunds;
  top_plans: MetricsTopPlan[];
  top_gateways: MetricsTopGateway[];
}

export interface CohortCell {
  offset: number;
  users: number;
  pct: number;
}
export interface CohortRow {
  cohort: string;
  size: number;
  retention: CohortCell[];
}
export interface CohortsResponse {
  cohorts: CohortRow[];
  max_offset: number;
}

export const usersAdminApi = {
  list: (params: {
    limit?: number; offset?: number; search?: string; blocked?: boolean;
    role?: number; sort?: string; order?: string; expiring?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    if (params.search) qs.set("search", params.search);
    if (params.blocked != null) qs.set("blocked", String(params.blocked));
    if (params.role != null) qs.set("role", String(params.role));
    if (params.sort) qs.set("sort", params.sort);
    if (params.order) qs.set("order", params.order);
    if (params.expiring != null) qs.set("expiring_days", String(params.expiring));
    return adminApi.get<PaginatedResponse<AdminUser>>(`/users?${qs}`);
  },
  get: (id: number) => adminApi.get<AdminUserDetail>(`/users/${id}`),
  logins: (id: number) => adminApi.get<LoginHistory>(`/users/${id}/logins`),
  referrals: (id: number) => adminApi.get<UserReferrals>(`/users/${id}/referrals`),
  trafficByNode: (id: number, days = 30) =>
    adminApi.get<TrafficByNode>(`/users/${id}/traffic-by-node?days=${days}`),
  block: (id: number, is_blocked: boolean) =>
    adminApi.put<{ success: boolean; is_blocked: boolean }>(`/users/${id}/block`, { is_blocked }),
  bulkAction: (params: {
    action: "points" | "discount" | "block" | "unblock";
    value?: number; search?: string; blocked?: boolean; role?: number; expiring?: number;
  }) =>
    adminApi.post<{ matched: number; applied: number }>("/users/bulk-action", {
      action: params.action,
      value: params.value ?? 0,
      search: params.search,
      blocked: params.blocked,
      role: params.role,
      expiring_days: params.expiring,
    }),
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
  /** Удаление человека целиком. `confirm` — слово подтверждения («УДАЛИТЬ»),
   *  его же спрашивают у человека при самоудалении. Ответ говорит, как вышло:
   *  `purged` — записи больше нет, `anonymized` — осталась обезличенной, потому
   *  что за человеком есть платежи и отчётность их терять нельзя. */
  remove: (id: number, confirm: string) =>
    adminApi.post<{ success: boolean; mode: "purged" | "anonymized"; panel_accounts_removed: number }>(
      `/users/${id}/delete`,
      { confirm },
    ),
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

// ---------- Массовые задачи «Пользователей»: «+N дней» и «Написать» ----------

/** Фильтры списка пользователей — ровно те, что стоят на странице. */
export interface BulkFilters {
  search?: string;
  blocked?: boolean;
  role?: number;
  expiring?: number;
}

export type BulkChannel = "telegram" | "cabinet" | "email";

export interface BulkDaysPreview {
  matched: number;
  apply: number;
  apply_frozen: number;
  /** Недавно платили или меняли подписку: обработаем в конце прохода. */
  deferred: number;
  skipped: Record<string, number>;
  recently_extended: { count: number; job_id: number | null; days: number | null };
  sample: { name: string | null; expire_at?: string; new_expire_at?: string }[];
  segment_hash: string;
  active_job_id: number | null;
  limits: { max_days: number; max_users: number };
}

export interface BulkMessagePreview {
  matched: number;
  recipients: number;
  skipped: Record<string, number>;
  by_channel: {
    telegram: number;
    telegram_bot_blocked: number;
    push_only: number;
    email_only: number;
    cabinet_only: number;
    unreachable: number;
  };
  email_enabled: boolean;
  segment_hash: string;
  active_job_id: number | null;
}

export type BulkJobStatus = "QUEUED" | "PROCESSING" | "PAUSED" | "CANCELING" | "COMPLETED" | "CANCELED" | "ERROR";

export interface BulkJob {
  id: number;
  kind: "days" | "message";
  status: BulkJobStatus;
  /** null у read-only админа. */
  created_by: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  done: number;
  applied: number;
  skipped: number;
  failed: number;
  unknown: number;
  verify_flagged: number;
  params: {
    days: number | null;
    include_trial: boolean | null;
    include_limited: boolean | null;
    channels: BulkChannel[] | null;
    text_preview: string | null;
  };
  pause_reason: string | null;
  parent_job_id: number | null;
  child_job_id: number | null;
  breakdown: Record<string, number>;
}

export interface BulkJobItem {
  user_id: number | null;
  name: string | null;
  status: string;
  category: string | null;
  reason: string | null;
  old_expire_at: string | null;
  target_expire_at: string | null;
  channels: string | null;
  verify_note: string | null;
  error: string | null;
}

export interface BulkStartResult {
  job_id: number;
  status: BulkJobStatus;
  total: number;
  apply?: number;
  recipients?: number;
  duplicate: boolean;
}

function bulkFilterQuery(f: BulkFilters, qs: URLSearchParams): URLSearchParams {
  if (f.search) qs.set("search", f.search);
  if (f.blocked != null) qs.set("blocked", String(f.blocked));
  if (f.role != null) qs.set("role", String(f.role));
  if (f.expiring != null) qs.set("expiring_days", String(f.expiring));
  return qs;
}

function bulkFilterBody(f: BulkFilters) {
  return {
    search: f.search || null,
    blocked: f.blocked ?? null,
    role: f.role ?? null,
    expiring_days: f.expiring ?? null,
  };
}

export const bulkJobsAdminApi = {
  daysPreview: (f: BulkFilters, p: { days: number; include_trial: boolean; include_limited: boolean }) => {
    const qs = bulkFilterQuery(f, new URLSearchParams());
    qs.set("days", String(p.days));
    qs.set("include_trial", String(p.include_trial));
    qs.set("include_limited", String(p.include_limited));
    return adminApi.get<BulkDaysPreview>(`/users/bulk/days/preview?${qs}`);
  },
  startDays: (
    f: BulkFilters,
    p: {
      days: number;
      include_trial: boolean;
      include_limited: boolean;
      allow_repeat: boolean;
      segment_hash: string;
      expected_apply: number;
      request_id: string;
      notify: { text: string; channels: BulkChannel[] } | null;
    },
  ) => adminApi.post<BulkStartResult>("/users/bulk/days", { ...bulkFilterBody(f), ...p }),
  messagePreview: (f: BulkFilters | null, p: { channels: BulkChannel[]; source_job_id?: number | null }) => {
    const qs = f ? bulkFilterQuery(f, new URLSearchParams()) : new URLSearchParams();
    if (p.source_job_id != null) qs.set("source_job_id", String(p.source_job_id));
    qs.set("channels", p.channels.join(","));
    return adminApi.get<BulkMessagePreview>(`/users/bulk/message/preview?${qs}`);
  },
  startMessage: (
    f: BulkFilters | null,
    p: {
      text: string;
      channels: BulkChannel[];
      source_job_id: number | null;
      segment_hash: string;
      expected_recipients: number;
      request_id: string;
      allow_repeat: boolean;
    },
  ) => adminApi.post<BulkStartResult>("/users/bulk/message", { ...(f ? bulkFilterBody(f) : {}), ...p }),
  testMessage: (text: string) =>
    adminApi.post<{ telegram: boolean; reason: "no_telegram" | "send_failed" | null }>(
      "/users/bulk/message/test",
      { text },
    ),
  jobs: (limit = 10) =>
    adminApi.get<{ items: BulkJob[]; active: { days: number | null; message: number | null } }>(
      `/users/bulk/jobs?limit=${limit}`,
    ),
  job: (jobId: number) => adminApi.get<BulkJob>(`/users/bulk/jobs/${jobId}`),
  items: (jobId: number, statuses: string[], limit = 100, offset = 0) => {
    const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (statuses.length) qs.set("status", statuses.join(","));
    return adminApi.get<{ total: number; items: BulkJobItem[] }>(`/users/bulk/jobs/${jobId}/items?${qs}`);
  },
  cancel: (jobId: number) =>
    adminApi.post<{ job_id: number; status: BulkJobStatus }>(`/users/bulk/jobs/${jobId}/cancel`),
  resume: (jobId: number) =>
    adminApi.post<{ job_id: number; status: BulkJobStatus }>(`/users/bulk/jobs/${jobId}/resume`),
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

export interface AbuseTrialsResponse {
  clusters: AbuseCluster[];
  total: number;
  /** Чем именно ищет ПОДКЛЮЧЁННЫЙ бэкенд. Шлёт только тот, у кого набор сигналов
   *  другой: адаптер поверх чужого бота, который не хранит адресов входа, — там
   *  групп по общему IP не бывает вовсе. Поля нет (наш бэкенд его не шлёт) —
   *  показываем прежнюю подсказку, она для нас верна. */
  note?: string;
  /** Что не сработало ИМЕННО СЕЙЧАС (панель не ответила, обход не дочитан).
   *  Без этого пустой список читался бы как «всё чисто». */
  warning?: string;
}

export const abuseAdminApi = {
  trials: (params: { min_accounts?: number; only_trial?: boolean } = {}) => {
    const qs = new URLSearchParams();
    if (params.min_accounts != null) qs.set("min_accounts", String(params.min_accounts));
    if (params.only_trial != null) qs.set("only_trial", String(params.only_trial));
    return adminApi.get<AbuseTrialsResponse>(`/abuse/trials?${qs}`);
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
  installed?: boolean; // false → версия новее установленной (доступна, ещё не стоит)
}
// Команда обновления и когда именно она подходит (каталог, сервер, режим установки).
export interface UpdateCommand {
  cmd: string;
  when?: string | null;
}
// Одна из обновляемых частей установки: кабинет или обслуживающий её бот.
export interface UpdateChannel {
  title: string;
  current: string | null; // null → версия бэкенду не видна (он так и скажет в note)
  latest: string | null;
  update_available: boolean;
  repo?: string;
  note?: string | null; // честная причина, если версия или лента неполные
  commands?: UpdateCommand[];
  items: UpdateItem[];
}
export interface UpdatesInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
  repo: string;
  items: UpdateItem[];
  // Ниже — НЕОБЯЗАТЕЛЬНОЕ. Кабинет и бот — разные продукты с разными релизами и
  // разными командами обновления; бэкенд, который это различает (адаптер поверх
  // чужого бота), присылает два блока и словами описывает расхождение между ними.
  // Наш бэкенд полей не шлёт: у него кабинет и бот — один релиз и одна команда,
  // поэтому экран остаётся прежним, с полями выше.
  cabinet?: UpdateChannel;
  bot?: UpdateChannel;
  mismatch?: string | null;
}
export const updatesAdminApi = {
  // force=true (кнопка «Проверить») — обходит серверный кэш для моментальной проверки.
  get: (force = false) => adminApi.get<UpdatesInfo>(`/updates${force ? "?force=1" : ""}`),
  // Только установленная версия — без похода на GitHub (для бейджа в шапке).
  version: () => adminApi.get<{ version: string }>("/updates/version"),
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
  // Только у рассылки «Истекают скоро» (audience TG_EXPIRING): на сколько дней вперёд.
  expiring_days?: number | null;
}

export type BroadcastChannel =
  | "TG_ALL" | "TG_PLAN" | "TG_SUBSCRIBED" | "TG_UNSUBSCRIBED" | "TG_TRIAL" | "TG_EXPIRED" | "TG_EXPIRING"
  | "EMAIL_ALL" | "EMAIL_SUBSCRIBED" | "EMAIL_TRIAL" | "EMAIL_EXPIRING" | "EMAIL_EXPIRED";

export const broadcastsAdminApi = {
  list: () => adminApi.get<{ items: AdminBroadcast[]; total: number }>("/broadcasts"),
  get: (task_id: string) => adminApi.get<AdminBroadcast>(`/broadcasts/${task_id}`),
  // planId нужен только каналу TG_PLAN, expiringDays — только TG_EXPIRING: размер
  // этих аудиторий зависит от выбора, поэтому счётчики перезапрашиваются при его смене.
  audienceCounts: (planId?: number, expiringDays?: number) => {
    const q = new URLSearchParams();
    if (planId) q.set("plan_id", String(planId));
    if (expiringDays) q.set("expiring_days", String(expiringDays));
    const qs = q.toString();
    return adminApi.get<Record<BroadcastChannel, number>>(
      qs ? `/broadcasts/audience-counts?${qs}` : "/broadcasts/audience-counts",
    );
  },
  create: (text: string, channels: BroadcastChannel[], planId?: number, expiringDays?: number) =>
    adminApi.post<{ telegram: string[]; email: number[] }>("/broadcasts", {
      text,
      channels,
      plan_id: planId,
      expiring_days: expiringDays,
    }),
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

/** Что на подключённом бэкенде применимо. `supported: false` — такого поля у него
 *  нет вовсе, и рисовать его нельзя: человек правил бы настройку, которой не
 *  существует. `locked` — поле есть и значение настоящее, но правится не отсюда:
 *  показываем выключенным и подписываем причиной. Карт нет — бэкенд наш, экран
 *  работает как раньше. Тот же договор, что у «Настроек» и «Меню бота». */
export interface TopupApplicability {
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
}

/** Правила пополнения ОДНОГО способа оплаты.
 *
 *  Надстройка поверх «Пополнения» для бэкендов, где условия пополнения живут не
 *  одним блоком на сервис, а у каждого способа свои (адаптер поверх чужого бота).
 *  Наш собственный бэкенд этих путей не знает и отвечает на них 404 — тогда
 *  карточка просто не показывается, а раздел остаётся прежним.
 *
 *  Суммы — В РУБЛЯХ и могут быть дробными (у бота они хранятся в копейках).
 *  `*_custom: false` означает «значение по умолчанию этого способа», и поле на
 *  экране остаётся пустым с подсказкой-умолчанием: цифра в пустом поле читалась
 *  бы как своя настройка. */
export interface TopupMethodRules {
  method_id: string;
  name: string;
  is_active: boolean;
  is_configured: boolean;
  order_index: number;
  min_amount: number;
  max_amount: number;
  min_default: number;
  max_default: number;
  min_custom: boolean;
  max_custom: boolean;
  /** Быстрые суммы. Пустой список при `presets_custom: true` — «кнопок нет». */
  presets: number[];
  presets_default: number[];
  presets_custom: boolean;
  /** Предупреждения бэкенда по этому способу: то, что бот сделает молча. */
  hints: string[];
}

export interface TopupMethodsResponse {
  items: TopupMethodRules[];
  limits: {
    presets_max_count: number;
    preset_max_amount: number;
    cabinet_min_amount: number;
    cabinet_max_amount: number;
  };
  note?: string;
  /** Применимость полей СВОДНОЙ карточки пополнения (TopupSettingsCard). */
  summary?: TopupApplicability;
  /** Применимость полей карточки способов. */
  methods?: TopupApplicability;
}

/** null в теле — «вернуть значение по умолчанию»; ключа нет — «не трогать».
 *  Для `presets` пустой список — это «кнопок нет», а не сброс: разные вещи. */
export interface TopupMethodUpdate {
  min_amount?: number | null;
  max_amount?: number | null;
  presets?: number[] | null;
}

export const topupMethodsAdminApi = {
  get: () => adminApi.get<TopupMethodsResponse>("/topup/methods"),
  update: (methodId: string, data: TopupMethodUpdate) =>
    adminApi.put<TopupMethodRules>(`/topup/methods/${encodeURIComponent(methodId)}`, data),
};

// ---------- Скидка на первую покупку триальщикам ----------

export interface TrialDiscountConfig {
  enabled: boolean;
  percent: number;
  days_before: number;
  lifetime_hours: number;
  /** Как скидка достаётся человеку на ПОДКЛЮЧЁННОМ бэкенде. Шлёт только тот, у
   *  кого это работает иначе, чем у нас (адаптер поверх чужого бота: там скидка
   *  приходит промокодом, а не ложится на аккаунт молча). Поля нет — описание
   *  экрана и так верно. Сюда же бэкенд кладёт свои предупреждения (рассылка
   *  включена, но остановлена; невручённые промокоды) — печатаем дословно. */
  note?: string;
  /** Есть ли у бэкенда холостой прогон (кнопка «Проверить»). Наш собственный
   *  бэкенд поля не шлёт вовсе — кнопки тогда нет, экран прежний. */
  can_dry_run?: boolean;
  /** Почему рассылка молчит при включённом тумблере. Только адаптер. */
  last_error?: string;
}

/** Ответ холостого прогона: кого зацепит рассылка, если её включить. Только у
 *  бэкендов с `can_dry_run` — у нашего такой ручки нет. */
export interface TrialDiscountDryRun {
  examined: number;
  candidates: number;
  unreachable: number;
  already_offered: number;
  has_discount?: number;
  truncated: boolean;
  skipped?: Record<string, number>;
  previews: {
    user_id: number;
    telegram_id: number | null;
    trial_ends: string | null;
    would_send: boolean;
    skip: string | null;
    valid_until?: string;
    text?: string;
  }[];
  discount_check?: string;
  lifetime_note?: string;
  orphan_codes?: { id: number | null; code: string | null; valid_until?: string | null }[];
  orphan_note?: string;
  last_error?: string;
}

export const trialDiscountAdminApi = {
  get: () => adminApi.get<TrialDiscountConfig>("/trial-discount"),
  update: (data: Partial<TrialDiscountConfig>) =>
    adminApi.put<TrialDiscountConfig>("/trial-discount", data),
  dryRun: () => adminApi.post<TrialDiscountDryRun>("/trial-discount/dry-run", {}),
};

// ---------- Резервный доступ истёкшим (1 ГБ на N дней) ----------

export interface ReserveConfig {
  enabled: boolean;
  reserve_gb: number;
  window_days: number;
  squad_uuid: string;
  // Всё ниже — НЕОБЯЗАТЕЛЬНОЕ: шлёт только адаптер поверх чужого бота, у которого
  // резерв устроен богаче (режимы вместо тумблера, срок в часах, два сквада).
  // Наш собственный бэкенд этих полей не отдаёт вовсе, и карточка тогда обязана
  // выглядеть и работать ровно как раньше.
  mode?: string;
  modes?: { value: string; label: string; desc?: string }[];
  mode_note?: string;
  window_hours?: number;
  squad_uuid_limited?: string;
  trial_enabled?: boolean;
  daily_enabled?: boolean;
  free_enabled?: boolean;
  // Тот же договор, что у «Настроек»: чего у бэкенда нет — не рисуем (supported),
  // что есть, но правится не отсюда — рисуем выключенным с причиной (locked).
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
}

// Кто сейчас на резерве. Своя таблица знает только «резерв выдавали»; работает он
// или нет, знает панель — поэтому у активных резервов бэкенд подмешивает их реальное
// состояние (panel) и готовую причину, если доступа по факту нет (problem).
export interface ReserveGrant {
  // Выдач у одного человека может быть несколько — резерв положен на каждое
  // истечение подписки, поэтому строку различает id, а не user_id.
  id: number;
  user_id: number;
  telegram_id: number | null;
  username: string | null;
  remna_uuid: string;
  granted_at: string | null;
  reserve_expire_at: string | null;
  ended: boolean;
  panel: { status: string; squads: string[]; traffic_limit_gb: number; used_traffic_gb: number } | null;
  problem: string | null;
  // Штатное состояние, не поломка (например «резерв израсходован») — рисуется
  // обычным текстом, без предупреждающего значка.
  note: string | null;
}

export interface ReserveGrants {
  items: ReserveGrant[];
  active: number;
  broken: number;
}

// Вердикт по сквад-резерву: отдаст он серверы или нет. checked=false — панель не
// ответила (это не приговор скваду, просто проверить не смогли).
export interface ReserveSquadCheck {
  checked: boolean;
  ok: boolean;
  name: string | null;
  hosts: number;
  problems: string[];
}

export const reserveAdminApi = {
  get: () => adminApi.get<ReserveConfig>("/reserve"),
  squadCheck: (squadUuid?: string) =>
    adminApi.get<ReserveSquadCheck>(`/reserve/squad-check${squadUuid ? `?squad_uuid=${encodeURIComponent(squadUuid)}` : ""}`),
  update: (data: Partial<ReserveConfig>) => adminApi.put<ReserveConfig>("/reserve", data),
  // Ручки нет у адаптера поверх чужого бота — вызывающий обязан молча прятать блок.
  grants: () => adminApi.get<ReserveGrants>("/reserve/grants"),
};

// ---------- Промо-баннер в кабинете ----------

export interface PromoBannerConfig {
  enabled: boolean;
  title: string;
  text: string;
  cta_text: string;
  cta_url: string;
  color: "accent" | "red" | "green" | "amber";
  audience: "all" | "no_sub" | "has_sub" | "trial" | "expiring";
  dismissible: boolean;
  starts_at: string;
  ends_at: string;
}

export const promoBannerAdminApi = {
  get: () => adminApi.get<PromoBannerConfig>("/promo-banner"),
  update: (data: Partial<PromoBannerConfig>) =>
    adminApi.put<PromoBannerConfig>("/promo-banner", data),
};

// ---------- Win-back истёкших ----------

export interface WinbackConfig {
  enabled: boolean;
  percent: number;
  days_after: number;
  lifetime_hours: number;
}

export const winbackAdminApi = {
  get: () => adminApi.get<WinbackConfig>("/winback"),
  update: (data: Partial<WinbackConfig>) => adminApi.put<WinbackConfig>("/winback", data),
};

// ---------- Скидка на продление до окончания подписки ----------

export interface RenewalDiscountConfig {
  enabled: boolean;
  percent: number;
  days_before: number;
  lifetime_hours: number;
  cooldown_days: number;
  skip_early_renewers: boolean;
  // Только чтение: нижняя граница дней (чтобы не совпасть с напоминаниями) и
  // предупреждение, если win-back щедрее.
  min_days_before?: number;
  note?: string | null;
}

export interface RenewalDiscountChannels {
  telegram: boolean;
  push: boolean;
  email: boolean;
}

export interface RenewalDiscountPreview {
  window_from: string;
  window_to: string;
  horizon_days: number;
  examined: number;
  would_grant: number;
  truncated: boolean;
  skipped: Record<string, number>;
  reason_labels: Record<string, string>;
  sample: {
    user_id: number | null;
    expire_at: string;
    grant_at: string;
    channels: RenewalDiscountChannels;
    would_grant: boolean;
    reason: string | null;
  }[];
  message: { telegram_html: string; push_title: string; push_body: string };
}

export interface RenewalDiscountStats {
  period_days: number;
  granted: number;
  used: number;
  expired: number;
  active: number;
  revoked: number;
  paid_rub: number;
  discount_given_rub: number;
  tg_failed: number;
  push_delivered: number;
  recent: {
    user_id: number | null;
    percent: number;
    granted_at: string;
    expires_at: string;
    status: string;
    tg_status: string | null;
  }[];
}

export type RenewalDiscountTelegramOutcome = "sent" | "no_telegram" | "blocked" | "failed";

export const renewalDiscountAdminApi = {
  get: () => adminApi.get<RenewalDiscountConfig>("/renewal-discount"),
  update: (data: Partial<RenewalDiscountConfig>) =>
    adminApi.put<RenewalDiscountConfig>("/renewal-discount", data),
  preview: (horizonDays: number) =>
    adminApi.get<RenewalDiscountPreview>(`/renewal-discount/preview?horizon_days=${horizonDays}`),
  stats: (days: number) => adminApi.get<RenewalDiscountStats>(`/renewal-discount/stats?days=${days}`),
  testSend: () =>
    adminApi.post<{ telegram: RenewalDiscountTelegramOutcome; push: number }>("/renewal-discount/test-send", {}),
  revokeActive: () => adminApi.post<{ revoked: number }>("/renewal-discount/revoke-active", { confirm: true }),
};

// ---------- Докупка +1 устройства к подписке ----------

/** Место под устройство в карточке пользователя. */
export interface AdminExtraSlot {
  id: number;
  subscription_id: number;
  status: "active" | "ended" | "burned" | "revoked" | string;
  starts_at: string | null;
  ends_at: string | null;
  end_reason: string | null;
  devices_removed: number;
}

export interface AdminExtraOrder {
  id: number;
  status: "pending" | "credited" | "applied" | "rejected" | string;
  kind: "new" | "extend" | string;
  source: "balance" | "gateway" | string;
  /** null — админ только для просмотра: суммы ему замаскированы. */
  amount: number | null;
  created_at: string | null;
  reason: string | null;
  slot_id: number | null;
}

export interface ExtraDeviceConfig {
  enabled: boolean;
  /** null — цена не задана, продажи закрыты даже при включённом тумблере. */
  price_rub_30d: number | null;
  min_amount_rub: number;
  min_days_left: number;
  max_extra: number;
  remove_excess_devices: boolean;
  notify_users: boolean;
  notify_admins: boolean;
}

/** Подсказка о цене: шаг между соседними тарифами за 30 дней и сколько в нём трафика. */
export interface ExtraDevicePriceHint {
  from_devices: number;
  to_devices: number;
  diff_30d_rub: number;
  traffic_diff_gb: number;
}

export interface ExtraDeviceAdminResponse {
  config: ExtraDeviceConfig;
  effective_enabled: boolean;
  hint: ExtraDevicePriceHint[];
  summary: {
    applied_30d?: number;
    amount_30d?: number;
    rejected_30d?: number;
    credited_open?: number;
    active_slots?: number;
  };
}

export const extraDeviceAdminApi = {
  get: () => adminApi.get<ExtraDeviceAdminResponse>("/extra-device"),
  update: (data: ExtraDeviceConfig) =>
    adminApi.put<{ config: ExtraDeviceConfig; effective_enabled: boolean }>("/extra-device", data),
};

// ---------- Докупка трафика ----------

/** Докупленный трафик в карточке пользователя: что действует и что уже кончилось. */
export interface AdminExtraTrafficGrant {
  id: number;
  subscription_id: number;
  status: string;
  gb: number;
  strategy: string;
  granted_at: string | null;
  /** null — стратегия без обновления трафика: прибавка живёт до продления. */
  ends_at: string | null;
  end_reason: string | null;
}

export interface AdminExtraTrafficOrder {
  id: number;
  status: string;
  source: string;
  gb: number;
  /** null — админ только для просмотра: суммы ему замаскированы. */
  amount: number | null;
  created_at: string | null;
  reason: string | null;
  grant_id: number | null;
}

export interface ExtraTrafficConfig {
  enabled: boolean;
  gb_per_purchase: number;
  /** null — цена не задана, продажи закрыты даже при включённом тумблере. */
  price_rub: number | null;
  min_amount_rub: number;
  show_from_percent: number;
  min_hours_left: number;
  max_gb_per_window: number;
  notify_users: boolean;
  notify_admins: boolean;
  notify_limited: boolean;
  refund_on_revoke: boolean;
}

/** Шаг между соседними тарифами за 30 дней: ориентир цены, а не сама цена. */
export interface ExtraTrafficPriceHint {
  from_gb: number;
  to_gb: number;
  diff_30d_rub: number;
  device_diff: number;
}

export interface ExtraTrafficAdminResponse {
  config: ExtraTrafficConfig;
  effective_enabled: boolean;
  hint: ExtraTrafficPriceHint[];
  /** Какие стратегии обновления трафика вообще встречаются у активных подписок. */
  strategies: { strategy: string; subscriptions: number }[];
  /** Есть подписки с ежедневным или еженедельным обновлением — окно короткое. */
  short_window: boolean;
  summary: {
    applied_30d?: number;
    gb_30d?: number;
    /** null — админ только для просмотра: суммы ему замаскированы. */
    amount_30d?: number | null;
    rejected_30d?: number;
    credited_open?: number;
    active_grants?: number;
    active_gb?: number;
  };
}

export const extraTrafficAdminApi = {
  get: () => adminApi.get<ExtraTrafficAdminResponse>("/extra-traffic"),
  update: (data: ExtraTrafficConfig) =>
    adminApi.put<{ config: ExtraTrafficConfig; effective_enabled: boolean }>("/extra-traffic", data),
};

// ---------- Напоминание о незавершённой оплате ----------

export interface PaymentReminderConfig {
  enabled: boolean;
  delay_minutes: number;
  max_age_minutes: number;
  cooldown_hours: number;
  max_per_30d: number;
  notify_admins: boolean;
}

export interface PaymentReminderAdminResponse {
  config: PaymentReminderConfig;
  effective_enabled: boolean;
  summary: { status: string; detail: string; count: number }[];
  conversion: { sent_30d?: number; paid_after_30d?: number };
  backlog: { invoices_30d?: number; people_30d?: number; amount_30d?: number };
}

export const paymentReminderAdminApi = {
  get: () => adminApi.get<PaymentReminderAdminResponse>("/payment-reminder"),
  update: (data: PaymentReminderConfig) =>
    adminApi.put<{ config: PaymentReminderConfig; effective_enabled: boolean }>(
      "/payment-reminder",
      data,
    ),
};

// ---------- Сигналы до ухода ----------

export interface ChurnSignalsConfig {
  check_enabled: boolean;
  check_delay_hours: number;
  idle_enabled: boolean;
  idle_days: number;
  idle_cooldown_days: number;
  idle_min_days_left: number;
}

export interface ChurnSignalsStats {
  days?: number;
  check_sent?: number;
  check_answered?: number;
  check_works?: number;
  check_broken?: number;
  check_failed?: number;
  /** Доля ответивших от спрошенных; null — спрашивать было некого. */
  check_answered_percent?: number | null;
  /** Доля «не работает» от ОТВЕТИВШИХ; null — ответов ещё нет. */
  check_broken_percent?: number | null;
  idle_sent?: number;
  idle_returned?: number;
  idle_failed?: number;
  idle_returned_percent?: number | null;
}

export interface ChurnSignalsLastRun {
  at: string;
  panel_ok: boolean;
  sent_check?: number;
  sent_idle?: number;
  failed?: number;
  errors?: number;
  returned?: number;
  skipped_check?: Record<string, number>;
  skipped_idle?: Record<string, number>;
}

export interface ChurnSignalsAdminResponse {
  config: ChurnSignalsConfig;
  check_window_hours: number;
  idle_window_days: number;
  stats: ChurnSignalsStats;
  broken: { user_id: number; answered_at: string | null }[];
  optouts: { check: number; idle: number };
  last_run: ChurnSignalsLastRun | null;
}

export const churnSignalsAdminApi = {
  get: () => adminApi.get<ChurnSignalsAdminResponse>("/churn-signals"),
  update: (data: ChurnSignalsConfig) =>
    adminApi.put<{ config: ChurnSignalsConfig }>("/churn-signals", data),
};

// ---------- Месячный дайджест пользователю ----------

export interface DigestConfig {
  enabled: boolean;
  day_of_month: number;
  hour: number;
}

export const digestAdminApi = {
  get: () => adminApi.get<DigestConfig>("/digest"),
  update: (data: Partial<DigestConfig>) => adminApi.put<DigestConfig>("/digest", data),
};

// Сводка письмом — тем, у кого нет ни Telegram, ни push. Отдельные ручки: у
// кабинета поверх чужого бота их нет (501), и карточка тогда не рисуется.

/** Итог последнего месяца рассылки по исходам. */
export interface DigestEmailLast {
  month: string;
  sent: number;
  failed: number;
  no_traffic: number;
  usage_error: number;
  over_limit: number;
  provider_blocked: number;
  sending: number;
}

export interface DigestEmailStatus {
  email_enabled: boolean;
  /** Адрес отправителя сводки; "" — основной. У «только просмотра» — "***". */
  email_from: string;
  /** С какого адреса письмо уйдёт на самом деле (пресеты Gmail/Яндекс/Mail.ru — с основного). */
  effective_from: string;
  /** Почта идёт через Brevo — отдельный адрес отправителя обязателен. */
  needs_separate_sender: boolean;
  digest_enabled: boolean;
  day_of_month: number;
  hour: number;
  audience: number;
  opted_out: number;
  max_per_run: number;
  /** Что мешает слать; непусто — включить нельзя и в день рассылки писем не будет. */
  blockers: string[];
  last: DigestEmailLast | null;
}

export interface DigestEmailPreview {
  subject: string;
  text: string;
  html: string;
}

export type DigestEmailOutcome = "would_send" | "no_traffic" | "usage_error" | "already_this_month";

export interface DigestEmailDryRun {
  audience: number;
  examined: number;
  truncated: boolean;
  would_send: number;
  blockers: string[];
  items: { user_id: number | null; gb: number | null; favorite: string | null; outcome: DigestEmailOutcome }[];
}

export const digestEmailAdminApi = {
  get: () => adminApi.get<DigestEmailStatus>("/digest/email"),
  update: (data: { email_enabled?: boolean; email_from?: string }) =>
    adminApi.put<DigestEmailStatus>("/digest/email", data),
  preview: (lang: "ru" | "en" = "ru") =>
    adminApi.get<DigestEmailPreview>(`/digest/email/preview?lang=${lang}`),
  dryRun: () => adminApi.get<DigestEmailDryRun>("/digest/email/dry-run"),
  test: (to: string) =>
    adminApi.post<{ success: boolean; to: string; from: string }>("/digest/email/test", { to }),
};

// ---------- Уведомление «трафик заканчивается» ----------

export interface TrafficAlertConfig {
  enabled: boolean;
  threshold_percent: number;
}

export const trafficAlertAdminApi = {
  get: () => adminApi.get<TrafficAlertConfig>("/traffic-alert"),
  update: (data: Partial<TrafficAlertConfig>) =>
    adminApi.put<TrafficAlertConfig>("/traffic-alert", data),
};

// ---------- Уведомление «новое устройство подключилось» ----------

export interface NewDeviceConfig {
  enabled: boolean;
}

export const newDeviceAdminApi = {
  get: () => adminApi.get<NewDeviceConfig>("/new-device"),
  update: (data: Partial<NewDeviceConfig>) => adminApi.put<NewDeviceConfig>("/new-device", data),
};

// ---------- Алерт пользователю о новом входе (новый IP/устройство) ----------

export interface LoginAlertConfig {
  enabled: boolean;
}

export const loginAlertAdminApi = {
  get: () => adminApi.get<LoginAlertConfig>("/login-alert"),
  update: (data: Partial<LoginAlertConfig>) =>
    adminApi.put<LoginAlertConfig>("/login-alert", data),
};

// ---------- Обязательная верификация email перед триалом/покупкой ----------

export interface EmailGateConfig {
  enabled: boolean;
}

export const emailGateAdminApi = {
  get: () => adminApi.get<EmailGateConfig>("/email-gate"),
  update: (data: Partial<EmailGateConfig>) => adminApi.put<EmailGateConfig>("/email-gate", data),
};

// ---------- Заморозка (пауза) подписки ----------

export interface FreezeConfig {
  enabled: boolean;
  max_days: number;
}

export const freezeAdminApi = {
  get: () => adminApi.get<FreezeConfig>("/freeze"),
  update: (data: Partial<FreezeConfig>) => adminApi.put<FreezeConfig>("/freeze", data),
};

// ---------- Импорт/экспорт настроек инсталляции ----------

export interface SettingsBundle {
  version: number;
  exported_at: string;
  assets: Record<string, unknown>;
}

// ---------- Ограничение админки по IP ----------

export interface AdminIpConfig {
  enabled: boolean;
  allowed_ips: string[];
  your_ip?: string;
}

export const adminIpApi = {
  get: () => adminApi.get<AdminIpConfig>("/admin-ip"),
  update: (data: Partial<AdminIpConfig>) => adminApi.put<AdminIpConfig>("/admin-ip", data),
};

// ---------- 2FA (TOTP) админа ----------

export interface TwoFactorSetup { secret: string; otpauth: string; }

export const twoFactorApi = {
  status: () => adminApi.get<{ enabled: boolean }>("/2fa/status"),
  setup: () => adminApi.post<TwoFactorSetup>("/2fa/setup", {}),
  enable: (code: string) => adminApi.post<{ enabled: boolean }>("/2fa/enable", { code }),
  unlock: (code: string) => adminApi.post<{ unlocked: boolean }>("/2fa/unlock", { code }),
  disable: (code: string) => adminApi.post<{ enabled: boolean }>("/2fa/disable", { code }),
};

export const settingsIoAdminApi = {
  export: () => adminApi.get<SettingsBundle>("/settings-io/export"),
  import: (bundle: SettingsBundle) =>
    adminApi.post<{ restored: string[]; skipped: string[]; count: number }>(
      "/settings-io/import",
      bundle,
    ),
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
  visible_nodes: string[]; // UUID нод для показа; [] = все
  service_keywords: string[]; // слова-заглушки в названии хоста; [] = ничего не прячем
}

export interface AdminPanelNode {
  uuid: string;
  name: string;
  country_code: string;
  online: boolean;
  disabled: boolean;
}

export const serverStatusAdminApi = {
  get: () => adminApi.get<ServerStatusConfig>("/server-status"),
  update: (data: Partial<ServerStatusConfig>) =>
    adminApi.put<ServerStatusConfig>("/server-status", data),
  nodes: () => adminApi.get<{ nodes: AdminPanelNode[] }>("/server-status/nodes"),
};

// ---------- Подписка в приложении (настройки панели Remnawave) ----------

export interface SubscriptionAppSettings {
  profile_title: string | null;
  support_link: string | null;
  profile_update_interval: number | null;
  is_profile_webpage_url_enabled: boolean | null;
  happ_announce: string | null;
  happ_routing: string | null;
  custom_response_headers: Record<string, string> | null;
  limits: { announce: number; title: number };
}

export const subscriptionAppAdminApi = {
  get: () => adminApi.get<SubscriptionAppSettings>("/subscription-app"),
  update: (data: Partial<Omit<SubscriptionAppSettings, "limits">>) =>
    adminApi.put<SubscriptionAppSettings>("/subscription-app", data),
  defaultRouting: () => adminApi.post<{ routing: string }>("/subscription-app/routing/default", {}),
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
  /** Новый порядок целиком: первый в списке — тот, что человек увидит первым и
   *  которым заплатит по умолчанию. Частичный список бэкенд не принимает. */
  reorder: (ids: number[]) =>
    adminApi.put<{ items: AdminGateway[]; total: number }>("/gateways/order", { ids }),
};

// ---------- Ad Links ----------

export interface AdminAdLink {
  id: number;
  name: string;
  code: string;
  is_active: boolean;
  created_at: string | null;
  /** Готовая ссылка от бэкенда, если он её знает: у разных ботов формат свой
   *  (наш бот — `t.me/бот?start=ad_код`, «Бедолага» — `?start=код`). Пусто —
   *  адрес бота сейчас не получить, показываем код. */
  url?: string;
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
  internal_squads: string[];
  external_squad: string | null;
  url: string;
  created_at: string | null;
  // Откуда взялся device_limit: сколько даёт тариф и сколько мест докуплено. Полей
  // нет (старый бот) — карточка показывает лимит как раньше, без расшифровки.
  plan_device_limit?: number | null;
  extra_devices_active?: number | null;
  extra_until?: string | null;
}

export interface AdminDevice {
  hwid: string;
  platform: string | null;
  device_model: string | null;
  os_version: string | null;
  user_agent: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUserTx {
  payment_id: string;
  status: string;
  gateway_type: string | null;
  purchase_type: string | null;
  is_test: boolean;
  amount: string | null;
  currency: string | null;
  plan_name: string | null;
  plan_duration: number | null;
  created_at: string | null;
  updated_at: string | null;
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
  resetTraffic: (userId: number) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/reset-traffic`),
  reissue: (userId: number) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/reissue`),
  referralReset: (userId: number) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/referral-reset`),
  devices: (userId: number) =>
    adminApi.get<{ devices: AdminDevice[]; count: number }>(`/subscriptions/user/${userId}/devices`),
  deleteDevice: (userId: number, hwid: string) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/devices/delete`, { hwid }),
  transactions: (userId: number, limit = 50) =>
    adminApi.get<{ items: AdminUserTx[] }>(`/subscriptions/user/${userId}/transactions?limit=${limit}`),
  setTrafficLimit: (userId: number, traffic_limit: number) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/traffic-limit`, { traffic_limit }),
  // revokeExtras — второй заход после 409: админ увидел, что у человека есть
  // оплаченные места, и подтвердил, что ставит лимит ниже вместе с их отменой.
  setDeviceLimit: (userId: number, device_limit: number, revoke_extras = false) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/device-limit`, {
      device_limit,
      revoke_extras,
    }),
  extraDevices: (userId: number) =>
    adminApi.get<{ slots: AdminExtraSlot[]; orders: AdminExtraOrder[] }>(
      `/subscriptions/user/${userId}/extra-devices`,
    ),
  revokeExtraDevice: (userId: number, slotId: number, refund_unused = false) =>
    adminApi.post<{ success: boolean; device_limit: number; refunded: number | null }>(
      `/subscriptions/user/${userId}/extra-devices/${slotId}/revoke`,
      { refund_unused },
    ),
  extraTraffic: (userId: number) =>
    adminApi.get<{ grants: AdminExtraTrafficGrant[]; orders: AdminExtraTrafficOrder[] }>(
      `/subscriptions/user/${userId}/extra-traffic`,
    ),
  // `refund` спрашивается КАЖДЫЙ раз и по умолчанию выключен (решение владельца):
  // отзыв прибавки — это его решение о деньгах, а не побочный эффект кнопки.
  revokeExtraTraffic: (userId: number, grantId: number, refund = false) =>
    adminApi.post<{ success: boolean; traffic_limit_gb: number; refunded: number | null }>(
      `/subscriptions/user/${userId}/extra-traffic/${grantId}/revoke`,
      { refund },
    ),
  squadToggle: (userId: number, squad_id: string, external = false) =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/squad-toggle`, { squad_id, external }),
  sync: (userId: number, direction: "from_remnawave" | "from_remnashop" = "from_remnawave") =>
    adminApi.post<{ success: boolean }>(`/subscriptions/user/${userId}/sync`, { direction }),
  sendMessage: (userId: number, text: string) =>
    adminApi.post<{ success: boolean; delivered: boolean; reason?: "no_telegram" | "send_failed" | null }>(
      `/subscriptions/user/${userId}/message`, { text }
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

export interface AuditFilters {
  limit?: number;
  actor?: string;
  method?: string;
  path?: string;
  date_from?: string;
  date_to?: string;
}

export const auditAdminApi = {
  list: (f: AuditFilters = {}) => {
    const q = new URLSearchParams();
    q.set("limit", String(f.limit ?? 200));
    if (f.actor) q.set("actor", f.actor);
    if (f.method) q.set("method", f.method);
    if (f.path) q.set("path", f.path);
    if (f.date_from) q.set("date_from", f.date_from);
    if (f.date_to) q.set("date_to", f.date_to);
    return adminApi.get<{ items: AuditEntry[] }>(`/audit?${q.toString()}`);
  },
};

// ---------- История уведомлений админам ----------
export interface AdminNotification {
  id: number;
  title: string;
  body: string;
  url: string;
  created_at: string | null;
}

export interface NotifSettings {
  admin_push_enabled: boolean;
  // Rich-вид админских уведомлений в Telegram. Поле НЕОБЯЗАТЕЛЬНОЕ: на установках
  // поверх чужого бота («Бедолага», адаптер) этого слоя нет вовсе и бэкенд поле не
  // отдаёт — тогда тумблер не рисуем совсем, чтобы не было мёртвой галки.
  admin_rich_enabled?: boolean;
}

export const notificationsAdminApi = {
  list: (limit = 100) =>
    adminApi.get<{ items: AdminNotification[] }>(`/notifications?limit=${limit}`),
  clear: () => adminApi.delete<{ ok: boolean }>("/notifications"),
  getSettings: () => adminApi.get<NotifSettings>("/notifications/settings"),
  // Шлём ТОЛЬКО тот тумблер, который дёрнули: бэкенд трактует отсутствующее поле
  // как «не менять», и переключение одного не затирает соседний.
  updateSettings: (body: Partial<Pick<NotifSettings, "admin_push_enabled" | "admin_rich_enabled">>) =>
    adminApi.put<NotifSettings>("/notifications/settings", body),
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
      } catch {
        /* тело ответа не JSON — оставляем дефолтный текст ошибки */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as { success: boolean; found: number; started: boolean };
  },
};
