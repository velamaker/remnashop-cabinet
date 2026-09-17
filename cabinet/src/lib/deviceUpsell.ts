import type {
  DevicesResponse,
  DurationGatewayPriceResponse,
  PlanOfferResponse,
  SubscriptionInfoResponse,
  SubscriptionOffersResponse,
} from "@/types/api";
import type { ChangeLoss } from "@/lib/planChange";
import { freeableSlots, sameDeviceGroups } from "@/lib/deviceGroups";
import { daysUntil } from "@/lib/format";

/**
 * «Нужно больше устройств?» — когда и какой тариф предлагать.
 *
 * ЗАЧЕМ. Лимит устройств заполнен — новое устройство не подключится, и панель сама
 * отправляет упёршегося человека на страницу «Устройства». Там он видел только
 * счётчик «2 из 2» и шёл в поддержку. Теперь рядом — ближайший тариф, где устройств
 * больше, со ссылкой на оплату.
 *
 * ГЛАВНОЕ ОГРАНИЧЕНИЕ. Смена тарифа у нас сжигает остаток срока (см. planChange).
 * Поэтому на «Устройствах» блок честно пишет, сколько дней пропадёт, а на Главной
 * появляется только тогда, когда пропадёт немного — иначе он звал бы платить и тут
 * же отговаривал.
 *
 * Все данные уже приходят с бэкенда (/current, /devices, /offers), новых ручек нет —
 * поэтому логика здесь, в чистых функциях, и заперта тестами.
 */

/** На Главной предлагаем смену, только если сгорит не больше недели. */
export const HOME_MAX_LOST_DAYS = 7;

export type DeviceLimitState = { kind: "ok" } | { kind: "free"; slots: number } | { kind: "full" };

const OK: DeviceLimitState = { kind: "ok" };

/**
 * Заполнен ли лимит и поможет ли уборка дублей.
 *
 * «free» — только если освобождение действительно снимает упор: лишние места занял
 * один аппарат (несколько приложений), и после уборки место под новое устройство
 * появится. Тогда тариф не продаём — человеку достаточно удалить лишнее. Если
 * дублей мало, чтобы выйти из упора, это «full»: подсказка про дубли остаётся, но и
 * тариф побольше честно нужен.
 */
export function deviceLimitState(
  sub: SubscriptionInfoResponse | null,
  devices: DevicesResponse | null,
): DeviceLimitState {
  if (!sub || !devices) return OK;
  // Истёкшей подписке нужен не тариф побольше, а продление (это RenewalBanner);
  // на паузе устройства не подключаются по другой причине.
  if (sub.status !== "ACTIVE" || sub.frozen === true) return OK;
  const max = devices.max_count;
  // 0 — без лимита.
  if (!(max > 0) || devices.current_count < max) return OK;

  const freeable = freeableSlots(sameDeviceGroups(devices.devices));
  if (freeable > 0 && devices.current_count - freeable < max) return { kind: "free", slots: freeable };
  return { kind: "full" };
}

export interface DeviceUpgrade {
  plan: PlanOfferResponse;
  days: number;
  price: DurationGatewayPriceResponse;
}

/** 0 — без лимита: такой тариф «больше» любого числа. */
const deviceRank = (limit: number) => (limit === 0 ? Number.POSITIVE_INFINITY : limit);

/**
 * Ближайший тариф, где устройств больше, чем сейчас.
 *
 * Правила (порядок важен):
 *  - только смена (не RENEW): продление того же тарифа лимит не поднимет;
 *  - устройств строго больше текущего лимита;
 *  - трафика не меньше текущего — иначе «побольше» оказался бы шагом назад;
 *  - срок — текущий, если он у тарифа есть, иначе самый короткий; цена на этот срок
 *    у первого шлюза обязательна (его же страница оплаты выбирает по умолчанию);
 *  - из подходящих — ближайший по устройствам, при равенстве — дешевле, дальше —
 *    порядок витрины.
 *
 * Бэкенд не сообщил условия смены (`plan_change_keeps_days`), подписка на паузе или
 * бессрочная — не предлагаем ничего: предупредить о потере мы бы не смогли, а
 * бессрочную сменой тарифа только испортить.
 */
export function pickDeviceUpgrade(
  offers: SubscriptionOffersResponse,
  current: { maxDevices: number; trafficLimit: number; durationDays: number },
): DeviceUpgrade | null {
  if (typeof offers.plan_change_keeps_days !== "boolean") return null;
  if (offers.current_frozen === true || offers.current_is_unlimited === true) return null;
  const gateway = offers.gateways[0]?.gateway_type;
  if (!gateway || !(current.maxDevices > 0)) return null;

  const candidates: (DeviceUpgrade & { index: number })[] = [];
  offers.plans.forEach((plan, index) => {
    if (plan.recommended_purchase_type === "RENEW") return;
    if (deviceRank(plan.device_limit) <= current.maxDevices) return;
    // Трафик в ГБ, 0 — безлимит.
    const trafficOk =
      current.trafficLimit === 0
        ? plan.traffic_limit === 0
        : plan.traffic_limit === 0 || plan.traffic_limit >= current.trafficLimit;
    if (!trafficOk) return;

    const terms = plan.durations.map((d) => d.days).filter((d) => d > 0);
    if (!terms.length) return;
    const days = terms.includes(current.durationDays) ? current.durationDays : Math.min(...terms);
    const price = plan.durations
      .find((d) => d.days === days)
      ?.prices.find((p) => p.gateway_type === gateway);
    if (!price) return;

    candidates.push({ plan, days, price, index });
  });

  candidates.sort(
    (a, b) =>
      deviceRank(a.plan.device_limit) - deviceRank(b.plan.device_limit) ||
      Number(a.price.final_amount) - Number(b.price.final_amount) ||
      a.index - b.index,
  );
  const best = candidates[0];
  return best ? { plan: best.plan, days: best.days, price: best.price } : null;
}

/**
 * Предпроверка Главной ДО запроса витрины: не триал и больше недели срока —
 * блок там всё равно не покажем, так что и /offers не дёргаем.
 */
export function homePrecheck(sub: SubscriptionInfoResponse): boolean {
  return sub.is_trial || daysUntil(sub.expire_at) <= HOME_MAX_LOST_DAYS;
}

/** Главная после ответа витрины: потеря не больше недели (или её нет вовсе). */
export function homeAllows(loss: ChangeLoss): boolean {
  if (loss === null) return true;
  if (loss.kind === "lifetime") return false;
  return loss.days <= HOME_MAX_LOST_DAYS;
}
