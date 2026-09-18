import type { ExtraTrafficResponse, SubscriptionOffersResponse } from "@/types/api";

/**
 * Докупка «+N ГБ к текущему периоду трафика» — что показывать и чем платить.
 *
 * ЗАЧЕМ. Трафик кончается задолго до конца оплаченного периода, а тариф побольше
 * человеку не нужен: ему нужны гигабайты на этот месяц, а не навсегда. Тогда объём
 * можно докупить к текущему окну за фиксированную цену.
 *
 * ГЛАВНОЕ ПРАВИЛО ЗДЕСЬ. Кнопку, которой нечем оплатить, не показываем (как и у
 * докупки устройства): баланса не хватает и рублёвых шлюзов нет — предложения НЕТ
 * вовсе. Показать «+50 ГБ за 50 ₽» и упереться в «платить нечем» хуже, чем молчать.
 *
 * ВТОРОЕ ПРАВИЛО — ПОРОГ. На Главной предложение появляется, только когда
 * израсходовано `show_from_percent` (умолчание 70 %) или трафик уже кончился.
 * Человеку с 10 % расхода предлагать докупку — шум. Порог считается по данным
 * /subscription/current, которые у кабинета уже есть: сама ручка предложения
 * дёргается ТОЛЬКО за порогом, иначе каждая отрисовка Главной стоила бы запроса
 * в панель.
 *
 * Всё считает бэкенд (цена, срок, право на покупку); здесь только чистые функции
 * показа — поэтому они и заперты тестами.
 */

/** Числа приходят строками (целые рубли) — сравниваем как числа, печатаем как есть. */
function num(value: string | undefined | null): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Порог по умолчанию — тот же, что в конфиге бэкенда (решение владельца Р-2). */
export const DEFAULT_SHOW_FROM_PERCENT = 70;

/**
 * Пора ли вообще спрашивать у бэкенда про докупку.
 *
 * Считается по данным подписки, без единого запроса: безлимиту не предлагаем
 * никогда, остальным — с порога расхода. Ровно 100 % и больше — всегда: это и есть
 * момент, когда предложение нужнее всего.
 */
export function shouldAskOffer(
  subscription: { traffic_limit: number; used_traffic_bytes?: number | null } | null | undefined,
  percent = DEFAULT_SHOW_FROM_PERCENT,
): boolean {
  if (!subscription || subscription.traffic_limit <= 0) return false;
  const limitBytes = subscription.traffic_limit * 1024 ** 3;
  const used = subscription.used_traffic_bytes ?? 0;
  return (used / limitBytes) * 100 >= percent;
}

export interface ExtraTrafficOfferView {
  gb: number;
  price: string;
  symbol: string;
  /** Момент обновления трафика. null — стратегия без обнуления. */
  until: string | null;
  /** До обновления меньше суток — предупреждаем отдельной строкой, а не мелочью. */
  endingSoon: boolean;
}

/** Предложение докупить. null — нельзя, нечем платить или выключено. */
export function offerView(data: ExtraTrafficResponse | null): ExtraTrafficOfferView | null {
  if (!data?.enabled || !data.offer?.available || !data.price || !data.gb) return null;
  const until = data.offer.until ?? null;
  return {
    gb: data.gb,
    price: data.price,
    symbol: data.currency_symbol ?? "",
    until,
    endingSoon: (data.offer.hours_left ?? Number.MAX_SAFE_INTEGER) < 24,
  };
}

export type PayOption = "balance" | "gateway";

export interface PayOptions {
  options: PayOption[];
  /** Баланса не хватает, но есть чем заплатить картой — говорим об этом прямо. */
  balanceLow: boolean;
  gateway: string | null;
}

/**
 * Чем человек может заплатить прямо сейчас.
 *
 * Пустой список означает «предложения нет»: показывать кнопку без способа оплаты
 * нельзя (см. шапку файла).
 */
export function payOptions(data: ExtraTrafficResponse | null, amount: string): PayOptions {
  const gateway = data?.gateways?.[0]?.gateway_type ?? null;
  const enough = num(data?.balance) >= num(amount);
  const options: PayOption[] = [];
  if (enough) options.push("balance");
  if (gateway) options.push("gateway");
  return { options, balanceLow: !enough && gateway !== null, gateway };
}

/** Сколько ГБ осталось до лимита. Отрицательного не показываем — только ноль. */
export function leftGb(data: ExtraTrafficResponse | null): number | null {
  if (!data?.enabled || data.used_bytes == null || !data.traffic_limit_gb) return null;
  const left = data.traffic_limit_gb - data.used_bytes / 1024 ** 3;
  return Math.max(0, Math.round(left * 10) / 10);
}

/**
 * Предупреждение на экране смены тарифа: докупленные ГБ сгорят.
 *
 * РЕШЕНИЕ ВЛАДЕЛЬЦА (Р-1): стоимость докупки при смене тарифа днями НЕ переносится,
 * но человек обязан узнать об этом ДО оплаты. Новый тариф сам обнуляет расход и даёт
 * больший объём, поэтому в убытке он не остаётся — но молчать об этом нельзя.
 *
 * Поля нет вовсе (старый бот, «Бедолага») — null: молчим, а не выдумываем.
 */
export function changeTrafficNote(
  offers: SubscriptionOffersResponse | null,
): { gb: number } | null {
  const gb = offers?.current_extra_traffic_gb;
  if (offers == null || gb == null || gb <= 0) return null;
  return { gb };
}
