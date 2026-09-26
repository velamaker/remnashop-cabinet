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
 * НА КНОПКЕ — НАСТОЯЩИЙ МИНИМУМ ПО ТАРИФАМ. Переключатель сроков общий для всех
 * карточек, а выгода у тарифов разная (цены округлены). В минимум идёт КАЖДЫЙ тариф,
 * у которого этот срок продаётся через этот шлюз, — с сырым процентом, до порога.
 * Порог «меньше 5 % не показываем» применяется один раз, к самому минимуму. Раньше
 * порог стоял у каждого тарифа, и тариф с выгодой 4 % из минимума просто выпадал:
 * кнопка 60 дней обещала «−6 %» по SOLO, хотя у HOME/FAMILY/TEAM на том же сроке
 * было 0–4 %. Тариф, у которого срок есть, а месячной цены нет, сравнить не с чем —
 * тогда кнопка молчит: обещать выгоду, которую нельзя проверить, мы не станем.
 */

/** Срок по умолчанию, если он есть у всей витрины (см. defaultTermDays). */
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

/** Сырая выгода срока против помесячной оплаты, % вниз до целого — без порога. */
function rawSavings(month: number, term: number, days: number): number {
  return Math.floor(100 - (100 * term) / ((month * days) / 30));
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
  const pct = rawSavings(month, term, days);
  return pct >= MIN_SHOWN_SAVINGS ? pct : null;
}

/**
 * Выгода для кнопки срока: минимум СЫРЫХ процентов по всем тарифам, где срок `days`
 * продаётся через шлюз `gw`, и порог — к этому минимуму. Null, если сравнивать не с
 * чем (срок не продаётся ни у кого или у кого-то из продающих нет месячной цены) либо
 * минимум ниже порога.
 */
export function termSavings(
  plans: PlanOfferResponse[],
  days: number,
  gw: PaymentGatewayType | null,
): number | null {
  if (gw == null || days <= 30) return null;
  let min: number | null = null;
  for (const plan of plans) {
    const term = finalAmount(plan, days, gw);
    if (term == null) continue; // у тарифа этот срок через этот шлюз не продаётся
    const month = finalAmount(plan, 30, gw);
    if (month == null) return null;
    const pct = rawSavings(month, term, days);
    if (min == null || pct < min) min = pct;
  }
  return min != null && min >= MIN_SHOWN_SAVINGS ? min : null;
}

/**
 * Срок по умолчанию: 90 дней — только если 90 есть у ВСЕХ тарифов витрины или хотя бы
 * у тарифа, который человек продлевает (recommended_purchase_type RENEW). Иначе —
 * первый срок первого тарифа, как было до выгоды на кнопках.
 *
 * Почему не «хоть у одного». Срок общий для всех карточек: если 90 дней продаётся
 * только у одного тарифа, остальные открывались бы на сроке, которого у них нет, —
 * без цены и с неактивной кнопкой оплаты. Свой тариф при продлении важнее остальной
 * витрины: его человек, скорее всего, и оплатит.
 */
export function defaultTermDays(plans: PlanOfferResponse[]): number | null {
  const has90 = (p: PlanOfferResponse) => p.durations.some((d) => d.days === DEFAULT_TERM_DAYS);
  const renew = plans.find((p) => p.recommended_purchase_type === "RENEW");
  if ((plans.length > 0 && plans.every(has90)) || (renew != null && has90(renew))) {
    return DEFAULT_TERM_DAYS;
  }
  return plans[0]?.durations[0]?.days ?? null;
}
