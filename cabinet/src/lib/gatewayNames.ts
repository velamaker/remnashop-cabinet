// Человеческие имена платёжных шлюзов для админки (админка по-русски, не i18n).
// Живут отдельным модулем, а не в AdminGatewaysPage: плитке «Возвраты» в
// «Статистике» они тоже нужны, а импорт из модуля страницы затянул бы её lazy-чанк
// в чанк дашборда.

export const GATEWAY_NAMES: Record<string, string> = {
  TELEGRAM_STARS: "Telegram Stars ⭐",
  YOOKASSA: "ЮKassa",
  YOOMONEY: "ЮMoney",
  CRYPTOMUS: "Cryptomus",
  HELEKET: "Heleket",
  CRYPTOPAY: "CryptoPay",
  FREEKASSA: "FreeKassa",
  MULENPAY: "MulenPay",
  PAYMASTER: "PayMaster",
  PLATEGA: "Platega",
  ROBOKASSA: "RoboKassa",
  URLPAY: "UrlPay",
  WATA: "Wata",
  VALUTIX: "Valutix",
};

/** Имя шлюза по его типу; незнакомый тип (новый шлюз базы) — как есть. */
export function gatewayName(type: string): string {
  return GATEWAY_NAMES[type] ?? type;
}
