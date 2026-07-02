import { adminApi } from "./admin";

// Состав и порядок кнопок главного меню бота (когда WEB_ENABLED=true).
export interface MenuConfig {
  cabinet_miniapp: boolean; // «Личный кабинет» через Mini App
  cabinet_url: boolean; // «Кабинет в браузере» — прямая ссылка
  connect_miniapp: boolean; // «Подключиться» → /devices в Mini App
  connect_url: boolean; // «Подключиться» → /devices ссылкой
  remna_sub: boolean; // «Подписка (резерв)» — стандартная сабка Remnawave
  order: string[]; // порядок кнопок (список ключей сверху вниз)
}

// Кнопки бота (авторские, 1-6) и их цвет — settings.menu.buttons[].color.
export interface BotButton {
  index: number;
  text: string;
  type: string;
  is_active: boolean;
  color: string | null; // 'primary' | 'success' | 'danger' | null (дефолт)
}
export interface BotButtons {
  buttons: BotButton[];
  colors: string[];
}

export const menuAdminApi = {
  get: () => adminApi.get<MenuConfig>("/menu"),
  update: (cfg: Partial<MenuConfig>) => adminApi.put<MenuConfig>("/menu", cfg),
  getButtons: () => adminApi.get<BotButtons>("/menu/buttons"),
  setButtonColors: (colors: Record<number, string | null>) =>
    adminApi.put<BotButtons>("/menu/buttons", { colors }),
};
