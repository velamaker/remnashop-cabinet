import { adminApi } from "./admin";

// Редактируемый текст письма с кодом. Подстановки: {brand}, {code}, {minutes}.
export interface EmailTemplate {
  subject: string;
  heading: string;
  intro: string;
  expire_note: string;
  ignore_note: string;
}

// Ответ бэкенда — сам шаблон плюс НЕОБЯЗАТЕЛЬНАЯ применимость. Держим её
// отдельным типом, чтобы `keyof EmailTemplate` по-прежнему означало «пять
// текстовых полей формы», а не карты применимости.
//   supported — поля у бэкенда нет вовсе: не рисуем;
//   locked — поле есть и значение настоящее, но правится не отсюда (поверх
//            «Бедолаги» письма живут в её редакторе шаблонов);
//   warnings — важное про поле, печатаем независимо от locked.
// Наш собственный бэкенд этих полей не присылает — экран тогда как раньше.
export interface EmailTemplateResponse extends EmailTemplate {
  supported?: Record<string, boolean>;
  locked?: Record<string, string>;
  warnings?: Record<string, string>;
}

export const emailTemplateAdminApi = {
  get: () => adminApi.get<EmailTemplateResponse>("/email-template"),
  update: (t: Partial<EmailTemplate>) =>
    adminApi.put<EmailTemplateResponse>("/email-template", t),
  sendTest: (to: string) =>
    adminApi.post<{ success: boolean; to: string }>("/email-template/test", { to }),
};
