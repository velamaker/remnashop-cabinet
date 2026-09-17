import type { Appearance } from "@/api/appearance";
import type { PlanOfferResponse, SubscriptionOffersResponse } from "@/types/api";

/**
 * Смена тарифа и что при ней сгорает.
 *
 * ЗАЧЕМ. Смена тарифа (CHANGE) у нашего бэкенда — срок С НУЛЯ: новый срок = момент
 * оплаты + длительность, остаток текущей подписки не переносится. У бессрочной
 * подписки CHANGE стоит на всех тарифах, и «навсегда» превращается в выбранный
 * срок. Бот об этом предупреждает, кабинет молчал — а блок «Нужно больше
 * устройств?» сам ведёт людей к смене тарифа. Поэтому страница оплаты честно
 * пишет, сколько дней пропадёт, и просит подтвердить.
 *
 * Условия смены сообщает бэкенд в /subscription/offers. Поля нет (чужой бэкенд,
 * старая сборка) — предупреждать не о чем: кабинет не знает, как там устроена
 * смена, и не выдумывает потерю.
 */

export type ChangeLoss = { kind: "days"; days: number } | { kind: "lifetime" } | null;

/** Что сгорит, если купить этот тариф. null — ничего (или бэкенд не сообщил). */
export function changeLoss(offers: SubscriptionOffersResponse, plan: PlanOfferResponse): ChangeLoss {
  if (typeof offers.plan_change_keeps_days !== "boolean" || offers.plan_change_keeps_days) return null;
  // RENEW продлевает от текущего срока, NEW — подписки ещё нет: терять нечего.
  if (plan.recommended_purchase_type !== "CHANGE") return null;
  // Бессрочную проверяем раньше дней: у неё счёта дней нет, а потеря самая большая.
  if (offers.current_is_unlimited === true) return { kind: "lifetime" };
  // Пробный период при покупке заменяется всегда, и это ожидаемо — не пугаем.
  if (offers.current_is_trial === true) return null;
  const days = offers.current_days_left;
  if (typeof days !== "number" || !Number.isFinite(days) || days < 1) return null;
  return { kind: "days", days: Math.floor(days) };
}

/** Ссылка на страницу оплаты с раскрытым тарифом и выбранным сроком. Сама не платит. */
export function billingHref(code: string, days: number): string {
  return `/billing?plan=${encodeURIComponent(code)}&days=${days}`;
}

/**
 * Разбор `?plan=&days=` на странице оплаты. Параметры из адресной строки — чужой
 * ввод: принимаем только код из витрины и срок, который у этого тарифа есть.
 * Всё остальное — поведение по умолчанию (бэкенд тариф и срок всё равно проверит).
 */
export function readBillingPreselect(
  params: URLSearchParams,
  offers: SubscriptionOffersResponse,
): { code: string | null; days: number | null } {
  const raw = params.get("plan");
  const plan = raw ? offers.plans.find((p) => p.public_code === raw) : undefined;
  if (!plan) return { code: null, days: null };

  const rawDays = params.get("days") ?? "";
  const n = /^\d{1,4}$/.test(rawDays) ? Number(rawDays) : 0;
  const days = n > 0 && plan.durations.some((d) => d.days === n) ? n : null;
  return { code: plan.public_code, days };
}

/** Тех-работы закрыли оплату (галка в оформлении, по умолчанию закрывают). */
export function paymentsBlocked(appearance: Appearance | null | undefined): boolean {
  return appearance?.maintenance === true && appearance?.maintenance_block_payments !== false;
}
