import { adminApi } from "./admin";

// Состав кнопок главного меню бота (когда WEB_ENABLED=true).
export interface MenuConfig {
  cabinet_miniapp: boolean; // «Личный кабинет» через Mini App
  cabinet_url: boolean; // «Кабинет в браузере» — прямая ссылка
  connect_miniapp: boolean; // «Подключиться» → /devices в Mini App
  connect_url: boolean; // «Подключиться» → /devices ссылкой
  remna_sub: boolean; // «Подписка (резерв)» — стандартная сабка Remnawave
}

export const menuAdminApi = {
  get: () => adminApi.get<MenuConfig>("/menu"),
  update: (cfg: Partial<MenuConfig>) => adminApi.put<MenuConfig>("/menu", cfg),
};
