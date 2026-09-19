import type { Appearance } from "@/api/appearance";
import type { PaymentGatewayType, PlanOfferResponse, SubscriptionOffersResponse } from "@/types/api";

/**
 * Смена тарифа: что будет с остатком текущей подписки.
 *
 * ЗАЧЕМ. У нашего бэкенда смена тарифа (CHANGE) пересчитывает остаток в дни нового
 * тарифа по цене дня (`plan_change_carry_active: true`, числа — в `plan_change_carry`).
 * `plan_change_keeps_days` — флаг для старых сборок: там он false, как только что-то
 * пропадает, чтобы старая сборка предупредила.
 * Если перенос выключен, смена начинает срок С НУЛЯ, и остаток сгорает; у бессрочной
 * подписки «навсегда» превращается в выбранный срок. Страница оплаты честно пишет,
 * что произойдёт, и просит подтвердить, только когда что-то пропадает.
 *
 * Условия смены сообщает бэкенд в /subscription/offers. Поля нет (чужой бэкенд,
 * старая сборка) — предупреждать не о чем: кабинет не знает, как там устроена
 * смена, и не выдумывает ни потерю, ни перенос.
 */

export type ChangeLoss = { kind: "days"; days: number } | { kind: "lifetime" } | null;

/**
 * Что будет с остатком при покупке этого тарифа на этот срок в этой валюте.
 *  - carry — остаток перенесётся: `bonus` дн. к новому сроку, `lost` дн. перенести нельзя;
 *  - lifetime — бессрочная подписка станет срочной;
 *  - days — остаток сгорит (перенос выключен, был возврат, нет цены срока);
 *  - null — терять нечего или бэкенд не сообщил условия.
 */
export type ChangeTerms =
  | {
      kind: "carry";
      left: number;
      bonus: number;
      lost: number;
      samePlan?: boolean;
      // cap — упор в предел переноса; иначе — цена прежних дней неизвестна.
      lostReason?: string | null;
    }
  | { kind: "lifetime" }
  | { kind: "days"; days: number }
  | null;

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

/**
 * Условия смены для карточки тарифа. Сервер всё равно пересчитает в момент оплаты —
 * это только показ, поэтому при любой неясности выбираем честную сторону («сгорит»).
 */
export function changeTerms(
  offers: SubscriptionOffersResponse,
  plan: PlanOfferResponse,
  days: number | null,
  currency: string | null,
): ChangeTerms {
  const carryActive = offers.plan_change_carry_active === true;
  if (typeof offers.plan_change_keeps_days !== "boolean" && !carryActive) return null;
  if (plan.recommended_purchase_type !== "CHANGE") return null;
  // Перенос выключен (или бэкенд без таблицы) — прежнее правило по старому флагу.
  if (!carryActive) return changeLoss(offers, plan);
  if (offers.current_is_unlimited === true) return { kind: "lifetime" };
  if (offers.current_is_trial === true) return null;
  // Резерв и «нечего переносить» (истекла, удалена) — молчим.
  if (offers.carry_mode === "reserve" || offers.carry_mode === "none") return null;
  const raw = offers.current_days_left;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 1) return null;
  const left = Math.floor(raw);
  // Переключатель сроков — объединение по всем тарифам: у этой карточки такого срока
  // может не быть (кнопка там и так выключена) — не пугаем «сгорит».
  if (days == null || !plan.durations.some((d) => d.days === days)) return null;
  // Был возврат — перенос отключён сервером.
  if (offers.carry_mode === "refund") return { kind: "days", days: left };
  const entry = (offers.plan_change_carry ?? []).find(
    (e) => e.plan_code === plan.public_code && e.duration_days === days && e.currency === currency,
  );
  if (!entry || entry.mode === "unpriced") return { kind: "days", days: left };
  if (entry.mode === "none") return null;
  const bonus = Math.max(0, Math.floor(entry.bonus_days));
  const lost = Math.max(0, Math.floor(entry.lost_days));
  const lostReason = lost > 0 ? (entry.capped ? "cap" : (entry.lost_reason ?? "old_price")) : null;
  if (entry.mode === "same_plan") return { kind: "carry", left, bonus, lost, samePlan: true, lostReason };
  return { kind: "carry", left, bonus, lost, lostReason };
}

/** Спрашивать ли подтверждение перед оплатой: только когда что-то пропадает. */
export function needsConfirm(terms: ChangeTerms, extraDevicesLost = false): boolean {
  // Докупленные места при смене тарифа сгорают всегда, но переспрашиваем только
  // когда их СТОИМОСТЬ не пересчитывается: «добавится днями» — не потеря.
  if (extraDevicesLost) return true;
  if (terms === null) return false;
  if (terms.kind === "carry") return terms.lost > 0;
  return true;
}

/** Валюта выбранного шлюза — по ней выбирается строка переноса. */
export function currencyOf(
  offers: SubscriptionOffersResponse,
  gateway: PaymentGatewayType | null,
): string | null {
  if (gateway == null) return null;
  return offers.gateways.find((g) => g.gateway_type === gateway)?.currency ?? null;
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
/**
 * Что раскрыть на «Оплате», если пришли по ссылке «Продлить» из бота.
 *
 * Бот не знает ни кода тарифа, ни срока — и знать не должен: ссылка в сообщении
 * статичная (`/billing?renew=1`), а какой тариф продлевать, решает сама витрина по
 * `recommended_purchase_type === "RENEW"`. Так ссылка не протухает при смене тарифа и
 * не зависит от того, что бот успел прочитать из базы.
 */
export function renewPreselect(
  offers: SubscriptionOffersResponse,
): { code: string | null; days: number | null } {
  const plan = offers.plans.find((p) => p.recommended_purchase_type === "RENEW");
  if (!plan) return { code: null, days: null };
  // Срок берём тот же, что у человека сейчас, если он ещё продаётся; иначе первый.
  const current = offers.current_days_left ?? null;
  const same = current ? plan.durations.find((d) => d.days === current) : undefined;
  return { code: plan.public_code, days: (same ?? plan.durations[0])?.days ?? null };
}

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
