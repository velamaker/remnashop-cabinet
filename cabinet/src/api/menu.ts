import { adminApi } from "./admin";

// Состав и порядок кнопок главного меню бота (когда WEB_ENABLED=true).
export interface MenuConfig {
  cabinet_miniapp: boolean; // «Личный кабинет» через Mini App
  cabinet_url: boolean; // «Кабинет в браузере» — прямая ссылка
  connect_miniapp: boolean; // «Подключиться» → /devices в Mini App
  connect_url: boolean; // «Подключиться» → /devices ссылкой
  remna_sub: boolean; // «Подписка (резерв)» — стандартная сабка Remnawave
  gift: boolean; // «Подарить подписку» — открывает выбор тарифа прямо в боте
  custom_miniapp: boolean; // своя мини-аппа (чужое приложение или своя страница подписки)
  custom_url?: string; // её адрес; только https, иначе Telegram не откроет
  order: string[]; // порядок кнопок (список ключей сверху вниз)
  texts?: Record<string, string>; // кастомные подписи по ключу (эмодзи ок)
  colors?: Record<string, string>; // цвет по ключу: primary|success|danger
  defaults?: Record<string, string>; // реальный текст по умолчанию (для превью/добавления эмодзи)
  /** Что применимо на этом бэкенде. Поля нет — применимо всё (наш бэкенд его не
   *  шлёт, поведение прежних установок не меняется). Нужен для чужих ботов: у них
   *  своё главное меню, и наши кнопки доступа туда не поставить — показывать форму,
   *  которая на сохранении отвечает отказом, нечестно. */
  supported?: { access_buttons?: boolean; gift?: boolean; bot_buttons?: boolean; nav?: boolean; colors?: boolean };
  /** Настоящие кнопки меню подключённого бота. Поля нет — рисуем свою фиксированную
   *  навигацию (наш бэкенд его не шлёт). У чужого бота меню своё, и показывать вместо
   *  него наши названия — вводить в заблуждение: человек правил бы кнопки, которых нет. */
  nav_items?: { key: string; label: string; enabled: boolean; color: string | null }[];
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
  // Сохранение текста и/или цвета кнопок бота (оба поля опциональны).
  saveButtons: (payload: {
    colors?: Record<number, string | null>;
    texts?: Record<number, string>;
  }) => adminApi.put<BotButtons>("/menu/buttons", payload),
};
