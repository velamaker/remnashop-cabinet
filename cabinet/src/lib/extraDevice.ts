import type {
  ExtraDeviceResponse,
  ExtraDeviceSlotResponse,
  SubscriptionOffersResponse,
} from "@/types/api";

/**
 * Докупка «+1 устройство до конца срока» — что показывать и чем платить.
 *
 * ЗАЧЕМ. Лимит устройств заполнен, а тариф побольше человеку не нужен: ему нужно одно
 * место, а не ещё 100 ГБ трафика. Тогда место можно докупить к текущей подписке и
 * заплатить только за оставшиеся дни. Блок встаёт между «освободите места» и
 * «тариф побольше»: сначала бесплатное, потом дешёвое, потом дорогое.
 *
 * ГЛАВНОЕ ПРАВИЛО ЗДЕСЬ. Кнопку, которой нечем оплатить, не показываем. Баланса не
 * хватает и рублёвых шлюзов нет — предложения НЕТ вовсе, и карточка честно падает на
 * тариф побольше. Показать «Докупить за 60 ₽» и упереться в «платить нечем» хуже, чем
 * не показывать ничего.
 *
 * Всё считает бэкенд (цена, право на покупку); здесь только чистые функции показа —
 * поэтому они и заперты тестами.
 */

/** Числа приходят строками (целые рубли) — сравниваем как числа, печатаем как есть. */
function num(value: string | undefined | null): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export interface ExtraDeviceOfferView {
  kind: "new" | "extend";
  slotId?: number;
  amount: string;
  until: string;
  days: number;
  symbol: string;
}

/** Предложение купить ещё одно место. null — нельзя, нечем платить или выключено. */
export function newOffer(data: ExtraDeviceResponse | null): ExtraDeviceOfferView | null {
  if (!data?.enabled || !data.new?.available || !data.new.amount || !data.new.until) return null;
  return {
    kind: "new",
    amount: data.new.amount,
    until: data.new.until,
    days: data.new.days ?? 0,
    symbol: data.currency_symbol ?? "",
  };
}

/** Докупленные места и «Продлить» к каждому. */
export function slotViews(data: ExtraDeviceResponse | null): {
  slot: ExtraDeviceSlotResponse;
  extend: ExtraDeviceOfferView | null;
}[] {
  if (!data?.enabled || !data.slots?.length) return [];
  return data.slots.map((slot) => ({
    slot,
    extend:
      slot.extend && slot.extend.amount && slot.extend.until
        ? {
            kind: "extend" as const,
            slotId: slot.slot_id,
            amount: slot.extend.amount,
            until: slot.extend.until,
            days: slot.extend.days,
            symbol: data.currency_symbol ?? "",
          }
        : null,
  }));
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
export function payOptions(
  data: ExtraDeviceResponse | null,
  amount: string,
): PayOptions {
  const gateway = data?.gateways?.[0]?.gateway_type ?? null;
  const enough = num(data?.balance) >= num(amount);
  const options: PayOption[] = [];
  if (enough) options.push("balance");
  if (gateway) options.push("gateway");
  return { options, balanceLow: !enough && gateway !== null, gateway };
}

/** Показывать ли «после этой даты лимит вернётся к N»: только если подписка длиннее. */
export function endsBefore(
  data: ExtraDeviceResponse | null,
  slot: ExtraDeviceSlotResponse,
): boolean {
  const until = data?.subscription_expire_at;
  if (!until) return false;
  return new Date(slot.ends_at).getTime() < new Date(until).getTime() - 86_400_000;
}

/** Причина отказа, при которой уместно предложить тариф побольше, а не молчать. */
export function suggestsBiggerPlan(data: ExtraDeviceResponse | null): boolean {
  const reason = data?.new?.reason;
  // `already_used` — решение владельца: место уже покупали и оно кончилось, второй раз
  // не предлагаем. `max_reached` — мест куплено сколько можно. И там и там дальше
  // выгоднее тариф, в котором ещё и трафик.
  return reason === "max_reached" || reason === "already_used";
}

export type ChangeExtraNote =
  | { kind: "carry"; count: number; limit: number }
  | { kind: "lost"; count: number; limit: number }
  | null;

/**
 * Что сказать при смене тарифа, если места докуплены.
 *
 * Полей нет вовсе (старый бот, «Бедолага») — null: молчим. Обещать «стоимость
 * добавится днями» можно ТОЛЬКО когда перенос остатка действительно включён и режим
 * текущей подписки — carry или same_plan; иначе честное «оплата не пересчитывается».
 */
export function changeExtraNote(
  offers: SubscriptionOffersResponse | null,
  newPlanDeviceLimit: number,
): ChangeExtraNote {
  const count = offers?.current_extra_devices;
  if (offers == null || count == null || count <= 0) return null;
  const carries =
    offers.plan_change_carry_active === true &&
    (offers.carry_mode === "carry" || offers.carry_mode === "same_plan");
  return { kind: carries ? "carry" : "lost", count, limit: newPlanDeviceLimit };
}

/** Есть ли место, которое переживёт продление и которое стоит продлить отдельно. */
export function renewExtraUntil(offers: SubscriptionOffersResponse | null): string | null {
  return offers?.current_extra_until ?? null;
}
