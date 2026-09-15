/**
 * Как показать заработок на рефералах, когда наград ДВЕ РАЗНЫЕ.
 *
 * ЗАЧЕМ. Раньше заработок был одним числом и одной единицей: либо рубли, либо дни
 * (по `reward_type`). У «Бедолаги» с их v4.5 ступень программы платит чем угодно и
 * в любом сочетании — процентом с платежа, фиксированной суммой, днями подписки, —
 * поэтому у человека одновременно бывает и рублёвый итог, и дни. Показать только
 * рубли значило бы написать «0 ₽» партнёру, которому исправно капают дни; показать
 * только дни — спрятать деньги.
 *
 * Функция чистая и живёт отдельно от экрана нарочно: одну и ту же цифру печатают
 * страница рефералки и карточка в боковой панели, и разойтись им нельзя.
 */
export function formatReferralEarned(
  earned: number,
  earnedDays: number | undefined,
  rewardType: string,
  fmt: { days: (n: number) => string; money: (n: number) => string },
): string {
  const days = Math.max(0, Math.trunc(earnedDays ?? 0));

  // EXTRA_DAYS — наш собственный бэкенд: там `earned` САМ измеряется в днях, а
  // отдельного поля нет вовсе. Смешивать эти две величины нельзя.
  if (rewardType === "EXTRA_DAYS") return fmt.days(earned);

  if (days > 0 && earned > 0) return `${fmt.money(earned)} + ${fmt.days(days)}`;
  if (days > 0) return fmt.days(days);
  return fmt.money(earned);
}

/** Есть ли что показывать вообще: ноль рублей и ноль дней — карточку не рисуем. */
export function hasReferralEarnings(
  earned: number,
  earnedDays: number | undefined,
): boolean {
  return earned > 0 || (earnedDays ?? 0) > 0;
}
