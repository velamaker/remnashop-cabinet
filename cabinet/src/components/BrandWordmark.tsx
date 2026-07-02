import { useBranding } from "@/contexts/BrandingContext";

/**
 * Логотип-текст бренда. Если название заканчивается коротким словом из заглавных
 * букв (например «VPN»), оно рендерится меньше и приглушённо, с отступом —
 * получается «Название VPN», где VPN визуально вторичен. Размер наследуется
 * от родителя (em), поэтому компонент масштабируется под место использования.
 */
export function BrandWordmark({ className = "" }: { className?: string }) {
  const { brandName } = useBranding();
  const parts = brandName.trim().split(/\s+/);
  const last = parts[parts.length - 1] ?? "";
  const hasSuffix = parts.length > 1 && /^[A-Z0-9]{2,4}$/.test(last);
  const main = hasSuffix ? parts.slice(0, -1).join(" ") : brandName;
  const suffix = hasSuffix ? last : null;

  return (
    <span className={`inline-flex flex-col leading-none ${className}`}>
      <span className="brand-wordmark font-bold tracking-tight">{main}</span>
      {suffix && (
        <span className="mt-[0.6em] text-[0.5em] font-semibold uppercase tracking-[0.24em] text-fg-subtle">
          {suffix}
        </span>
      )}
    </span>
  );
}
