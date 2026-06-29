import { ShieldCheck } from "lucide-react";
import { useBranding } from "@/contexts/BrandingContext";

/**
 * Знак бренда: если в админке загружен логотип (appearance.logo_url) —
 * показываем картинку; иначе дефолтную иконку-щит в цветной плашке.
 * Размер задаётся в px и масштабирует обе ветки одинаково.
 */
export function BrandLogo({
  size = 42,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  const { appearance } = useBranding();
  const logo = appearance?.logo_url;

  if (logo) {
    return (
      <img
        src={logo}
        alt=""
        style={{ width: size, height: size }}
        className={`rounded-2xl object-contain ${className}`}
      />
    );
  }

  return (
    <div
      style={{ width: size, height: size }}
      className={`brand-mark flex items-center justify-center rounded-2xl text-white ${className}`}
    >
      <ShieldCheck style={{ width: size * 0.52, height: size * 0.52 }} strokeWidth={2.2} />
    </div>
  );
}
