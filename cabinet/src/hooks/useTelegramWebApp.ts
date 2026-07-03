import { useEffect, useState } from "react";

interface TelegramWebApp {
  initData: string;
  colorScheme: "dark" | "light";
  ready: () => void;
  expand: () => void;
  openLink: (url: string, options?: { try_instant_view?: boolean }) => void;
  BackButton: {
    isVisible: boolean;
    show: () => void;
    hide: () => void;
    onClick: (fn: () => void) => void;
    offClick: (fn: () => void) => void;
  };
  onEvent: (event: string, fn: () => void) => void;
  offEvent: (event: string, fn: () => void) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
    // Выставляются inline-скриптом в index.html, пока асинхронно грузится SDK.
    __tgWebAppExpected?: boolean;
    __tgWebAppSettled?: boolean;
  }
}

export function getTelegramWebApp(): TelegramWebApp | null {
  const app = window.Telegram?.WebApp;
  return app && app.initData ? app : null;
}

/**
 * Дожидается окончания загрузки Telegram SDK, если страница открыта как Mini App
 * (SDK подгружается асинхронно — см. index.html). В обычном браузере и после
 * загрузки SDK резолвится немедленно. Нужен, чтобы авто-вход по initData не
 * срабатывал раньше, чем доступен window.Telegram.WebApp.
 */
export function whenTelegramReady(timeoutMs = 5000): Promise<void> {
  if (!window.__tgWebAppExpected || window.__tgWebAppSettled) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener("tg-webapp-settled", finish);
      resolve();
    };
    window.addEventListener("tg-webapp-settled", finish);
    // Страховка: не зависаем, если событие по какой-то причине не пришло.
    setTimeout(finish, timeoutMs);
  });
}

export function useIsMiniApp(): boolean {
  return Boolean(getTelegramWebApp());
}

/**
 * Переход по ссылке — deep-link кастомных схем (happ://, hiddify:// и т.п.)
 * внутри Telegram Mini App через обычный location.href не работает: встроенный
 * WebView не резолвит неизвестную схему сам (net::ERR_UNKNOWN_URL_SCHEME),
 * т.к. это top-level навигация через его собственный сетевой стек. Telegram.
 * WebApp.openLink передаёт ссылку самому клиенту Telegram, а он уже открывает
 * её через ОС (Intent/URL scheme) — так кастомные схемы открываются нормально.
 * Вне Mini App (обычный браузер) — как раньше, просто location.href.
 */
export function openExternalLink(url: string) {
  const app = getTelegramWebApp();
  if (app) {
    app.openLink(url, { try_instant_view: false });
  } else {
    window.location.href = url;
  }
}

export function useTelegramTheme(): "dark" | "light" | null {
  const [scheme, setScheme] = useState<"dark" | "light" | null>(() => {
    const app = getTelegramWebApp();
    return app ? app.colorScheme : null;
  });

  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;
    const handler = () => setScheme(app.colorScheme);
    app.onEvent("themeChanged", handler);
    return () => app.offEvent("themeChanged", handler);
  }, []);

  return scheme;
}

export function useTelegramBackButton(onBack: (() => void) | null) {
  useEffect(() => {
    const app = getTelegramWebApp();
    if (!app) return;

    if (onBack) {
      app.BackButton.show();
      app.BackButton.onClick(onBack);
    } else {
      app.BackButton.hide();
    }

    return () => {
      if (onBack) app.BackButton.offClick(onBack);
      app.BackButton.hide();
    };
  }, [onBack]);
}
