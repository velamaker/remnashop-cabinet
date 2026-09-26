import type { PaymentGatewayType, PlanOfferResponse } from "@/types/api";

/**
 * Выгода длинного срока — «−25 %» на кнопке срока в «Оплате».
 *
 * ЗАЧЕМ. Большинство покупок — на месяц, а скидка за длинный срок была незаметна:
 * цена 90 дней висела отдельной цифрой, и сравнивать её с месячной человек должен был
 * сам. Теперь выгода написана прямо на кнопке срока, а по умолчанию выбран 90-дневный
 * срок — самый ходовой из длинных.
 *
 * СЧИТАЕМ ОТ МЕСЯЧНОЙ ЦЕНЫ ЭТОГО ЖЕ ТАРИФА И ЭТОГО ЖЕ ШЛЮЗА, по итоговой сумме (после
 * личной скидки — она одинаково уменьшает обе цены и на процент не влияет). 365 дней —
 * это 365/30 месячных цен, а не 12: так цена года честно сравнивается с помесячной
 * оплатой того же срока.
 *
 * НА КНОПКЕ — МИНИМУМ ПО ТАРИФАМ. Переключатель сроков общий для всех карточек, а
 * выгода у тарифов немного разная (цены округлены). Минимум не обещает больше, чем
 * получит человек на любом тарифе.
 */

/** Срок, выбранный по умолчанию, если он есть хоть у одного тарифа. */
export const DEFAULT_TERM_DAYS = 90;

/** Меньше этого не показываем: «−2 %» — не выгода, а шум округления. */
export const MIN_SHOWN_SAVINGS = 5;

function finalAmount(plan: PlanOfferResponse, days: number, gw: PaymentGatewayType): number | null {
  const d = plan.durations.find((x) => x.days === days);
  const p = d?.prices.find((x) => x.gateway_type === gw);
  if (!p || p.is_free) return null;
  const n = Number(p.final_amount);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Выгода срока `days` у тарифа в процентах (вниз до целого) или null. */
export function planTermSavings(
  plan: PlanOfferResponse,
  days: number,
  gw: PaymentGatewayType | null,
): number | null {
  if (gw == null || days <= 30) return null;
  const month = finalAmount(plan, 30, gw);
  const term = finalAmount(plan, days, gw);
  if (month == null || term == null) return null;
  const pct = Math.floor(100 - (100 * term) / ((month * days) / 30));
  return pct >= MIN_SHOWN_SAVINGS ? pct : null;
}

/** Выгода для кнопки срока: минимум по тарифам, у которых она есть, иначе null. */
export function termSavings(
  plans: PlanOfferResponse[],
  days: number,
  gw: PaymentGatewayType | null,
): number | null {
  let min: number | null = null;
  for (const plan of plans) {
    const pct = planTermSavings(plan, days, gw);
    if (pct != null && (min == null || pct < min)) min = pct;
  }
  return min;
}

/** Срок по умолчанию: 90 дней, если он есть, иначе первый срок первого тарифа. */
export function defaultTermDays(plans: PlanOfferResponse[]): number | null {
  if (plans.some((p) => p.durations.some((d) => d.days === DEFAULT_TERM_DAYS))) {
    return DEFAULT_TERM_DAYS;
  }
  return plans[0]?.durations[0]?.days ?? null;
}
