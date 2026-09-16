// Одна запись денег на всю «Статистику» админки: блок «Продажи», графики по дням и
// плитка «Возвраты» стоят на одной странице, и «$5» рядом с «5 USD» читалось бы как
// две разные вещи. Раньше это были две одинаковые копии в AdminDashboardPage и
// DailyCharts.

// Форматирование выручки по валюте (RUB → ₽, USD → $, XTR → ⭐ звёзды Telegram).
export function formatAdminMoney(currency: string, amount: number): string {
  const n = amount.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  switch (currency) {
    case "RUB": return `${n} ₽`;
    case "USD": return `$${n}`;
    case "EUR": return `€${n}`;
    case "XTR": return `${n} ⭐`;
    default: return `${n} ${currency}`;
  }
}
