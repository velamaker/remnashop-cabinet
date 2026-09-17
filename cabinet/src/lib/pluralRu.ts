/** Русское склонение по числу: 1 день, 2 дня, 5 дней, 11 дней, 21 день. */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const n100 = Math.abs(n) % 100;
  const n10 = n100 % 10;
  if (n100 >= 11 && n100 <= 14) return many;
  if (n10 === 1) return one;
  if (n10 >= 2 && n10 <= 4) return few;
  return many;
}

/** «7 дней», «3 дня», «1 день» — для админки (она только на русском). */
export function ruDays(n: number): string {
  return `${n} ${pluralRu(n, "день", "дня", "дней")}`;
}
