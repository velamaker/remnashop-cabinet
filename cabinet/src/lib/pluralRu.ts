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

/**
 * Форма счётного слова по числу И ПО ЯЗЫКУ.
 *
 * Админка теперь русская и английская, а правила разные: «1 день / 2 дня / 5 дней»
 * против «1 day / 2 days». Раньше форму выбирал pluralRu независимо от языка — в
 * английском это давало «1 devices» и «21 day». Для нерусских языков правило
 * простое: один — одна форма, остальное — другая.
 */
export function pluralFor(
  lang: string,
  n: number,
  one: string,
  few: string,
  many: string,
): string {
  if (lang === "ru" || lang === "be" || lang === "uk") return pluralRu(n, one, few, many);
  return Math.abs(n) === 1 ? one : many;
}
