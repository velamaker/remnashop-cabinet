import { getTelegramWebApp, openExternalLink } from "@/hooks/useTelegramWebApp";

/**
 * Уйти на страницу оплаты.
 *
 * ЗАЧЕМ ОТДЕЛЬНАЯ ФУНКЦИЯ. Внутри мини-аппы `location.href = ...` уводит САМ
 * WebView на сайт платёжки. Кабинет при этом перестаёт существовать: стрелка
 * «назад» Telegram возвращать уже некуда, и человек остаётся запертым на странице
 * ЮMoney (жалоба владельца 19.09 — «не работает кнопка назад после оплаты»).
 *
 * Поэтому в мини-аппе счёт открываем НАРУЖУ (`Telegram.WebApp.openLink`): кабинет
 * остаётся жив под браузером, и закрыв его, человек возвращается к нам. В обычном
 * браузере всё как было — обычный переход на той же вкладке.
 *
 * Возвращает `true`, если счёт открыт во внешнем окне (кабинет остался на месте):
 * вызывающий тогда не показывает «сейчас перебросим», а пишет «оплатите и
 * вернитесь» и обновляет данные, когда человек вернулся.
 */
export function openPayment(url: string): boolean {
  if (!getTelegramWebApp()) {
    window.location.href = url;
    return false;
  }
  // Не удалось открыть (недопустимая схема) — лучше уж обычный переход, чем
  // кнопка, которая молча ничего не делает.
  if (!openExternalLink(url)) {
    window.location.href = url;
    return false;
  }
  return true;
}

/**
 * Позвать `onReturn`, когда человек вернулся в кабинет из внешнего окна оплаты.
 *
 * Платёжка после оплаты редиректит в ТОМ браузере, где её открыли, — до мини-аппы
 * это не доезжает. Единственный сигнал, что пора перечитать подписку и баланс, —
 * возвращение вкладки к жизни.
 */
export function onReturnFromPayment(onReturn: () => void): () => void {
  const handler = () => {
    if (document.visibilityState === "visible") onReturn();
  };
  document.addEventListener("visibilitychange", handler);
  window.addEventListener("focus", handler);
  return () => {
    document.removeEventListener("visibilitychange", handler);
    window.removeEventListener("focus", handler);
  };
}
