/**
 * Флаг страны — из локального пакета flag-icons (self-hosted SVG, как шрифты).
 *
 * Раньше флаги грузились с внешнего flagcdn.com — на части РФ-сетей его режет DPI,
 * и «некоторые флаги не подтягивались» (показывался alt-текст «PL»/«IL»). Теперь
 * SVG забандлены в сборку кабинета и отдаются со своего домена.
 *
 * Размер задаётся классами (h-/w-), скругление/тень/заливка — уже внутри.
 */
export function Flag({
  code,
  className = "h-3.5 w-5",
}: {
  code?: string | null;
  className?: string;
}) {
  const cc = (code || "").trim().toLowerCase();
  if (!/^[a-z]{2}$/.test(cc)) {
    // Неизвестный/пустой код страны — нейтральный глобус вместо «битого» флага.
    return (
      <span className={`inline-flex shrink-0 items-center justify-center ${className}`} aria-hidden>
        🌐
      </span>
    );
  }
  return (
    <span
      className={`fi fi-${cc} inline-block shrink-0 rounded-[2px] bg-cover bg-center shadow-sm ${className}`}
      aria-hidden
    />
  );
}
