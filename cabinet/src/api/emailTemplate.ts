import { adminApi } from "./admin";

// Редактируемый текст письма с кодом. Подстановки: {brand}, {code}, {minutes}.
export interface EmailTemplate {
  subject: string;
  heading: string;
  intro: string;
  expire_note: string;
  ignore_note: string;
}

export const emailTemplateAdminApi = {
  get: () => adminApi.get<EmailTemplate>("/email-template"),
  update: (t: Partial<EmailTemplate>) =>
    adminApi.put<EmailTemplate>("/email-template", t),
  sendTest: (to: string) =>
    adminApi.post<{ success: boolean; to: string }>("/email-template/test", { to }),
};
