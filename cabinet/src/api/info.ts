import { api } from "./client";
import { adminApi } from "./admin";

export interface FaqItem {
  q: string;
  a: string;
}

// Контент страницы «Информация». Тексты — в markdown (мини-рендер на фронте).
// Вкладка «Серверы» сюда не входит — она живая (Remnawave), см. service_status.
export interface InfoContent {
  faq: FaqItem[];
  rules: string;
  privacy: string;
  offer: string;
  statuses: string;
}

/**
 * Ответ админской ручки: тот же контент в корне (так его читал кабинет до
 * переводов) плюс язык и то, что реально сохранено именно для него.
 *
 * `own` — только сохранённое: редактор обязан отличать «перевели теми же словами»
 * от «не переводили вовсе», иначе он подставит русский текст в поле перевода и
 * первое же «Сохранить» превратит фолбэк в настоящий «перевод».
 */
export interface AdminInfoResponse extends InfoContent {
  lang: string;
  base_lang: string;
  own: Partial<InfoContent>;
  base: InfoContent;
  translated_langs: string[];
}

const withLang = (path: string, lang?: string | null) =>
  lang && lang !== "ru" ? `${path}?lang=${encodeURIComponent(lang)}` : path;

// Публичное чтение (сохранённое или брендированные дефолты).
export const infoApi = {
  get: (lang?: string | null) => api.get<InfoContent>(withLang("/info", lang)),
};

// Редактирование — только для админов.
export const infoAdminApi = {
  get: (lang?: string | null) => adminApi.get<AdminInfoResponse>(withLang("/info", lang)),
  update: (data: Partial<InfoContent>, lang?: string | null) =>
    adminApi.put<AdminInfoResponse>(withLang("/info", lang), data),
};
