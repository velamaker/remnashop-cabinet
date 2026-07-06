import { ApiError } from "@/types/api";
import { translate } from "@/i18n/translate";

// В проде nginx/Caddy проксирует /api на бэкенд бота (см. nginx.conf).
// В деве vite.config.ts делает то же самое на localhost.
const API_BASE = "/api";

let refreshPromise: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    return res.ok;
  } catch {
    return false;
  }
}

// Дедуплицируем одновременные refresh-запросы: если несколько вызовов API
// словили 401 параллельно, рефрешим токен только один раз.
function refreshOnce(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  skipAuthRetry?: boolean;
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const text = await res.text();
    if (!text) return res.statusText || translate("fmt.errStatus", { n: res.status });
    // Try JSON first
    try {
      const data = JSON.parse(text);
      if (typeof data?.detail === "string") return data.detail;
      if (Array.isArray(data?.detail)) return data.detail.map((d: any) => d.msg ?? d).join("; ");
      return JSON.stringify(data);
    } catch {
      // HTML or plain text — strip tags and return first meaningful line
      const stripped = text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 120);
      return stripped || res.statusText || translate("fmt.errStatus", { n: res.status });
    }
  } catch {
    return res.statusText || translate("fmt.errStatus", { n: res.status });
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, skipAuthRetry, headers, ...rest } = options;

  const init: RequestInit = {
    ...rest,
    credentials: "include",
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };

  let res = await fetch(`${API_BASE}${path}`, init);

  // Access token истёк — пробуем один раз рефрешнуть и повторить запрос.
  if (res.status === 401 && !skipAuthRetry && path !== "/auth/refresh") {
    const refreshed = await refreshOnce();
    if (refreshed) {
      res = await fetch(`${API_BASE}${path}`, init);
    }
  }

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};
