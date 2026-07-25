import { useEffect, useRef } from "react";
import type { TelegramAuthRequest } from "@/types/api";

declare global {
  interface Window {
    onTelegramAuth?: (user: TelegramAuthRequest) => void;
  }
}

interface TelegramLoginButtonProps {
  botUsername: string;
  onAuth: (data: TelegramAuthRequest) => void;
}

// Встраивает официальный Telegram Login Widget.
// Виджет сам подписывает данные ботовским токеном на сервере Telegram;
// backend (/auth/telegram) проверяет подпись hash перед тем как доверять данным.
export function TelegramLoginButton({
  botUsername,
  onAuth,
}: TelegramLoginButtonProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    window.onTelegramAuth = onAuth;
    // Копируем ref в локальную переменную — к моменту cleanup containerRef.current
    // может уже указывать на другой узел (react-hooks/exhaustive-deps).
    const container = containerRef.current;

    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", botUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-radius", "12");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    script.setAttribute("data-request-access", "write");

    container?.appendChild(script);

    return () => {
      delete window.onTelegramAuth;
      container?.replaceChildren();
    };
  }, [botUsername, onAuth]);

  return <div ref={containerRef} className="flex justify-center" />;
}
