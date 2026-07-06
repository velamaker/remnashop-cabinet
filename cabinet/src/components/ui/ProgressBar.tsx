import { clsx } from "clsx";

interface ProgressBarProps {
  value: number;
  max: number;
  className?: string;
}

export function ProgressBar({ value, max, className }: ProgressBarProps) {
  const percent = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const isHigh = percent >= 90;
  const isMedium = percent >= 70;

  return (
    <div className={clsx("h-2 w-full overflow-hidden rounded-full bg-bg-subtle", className)}>
      <div
        className={clsx(
          "h-full rounded-full bg-gradient-to-r shadow-[inset_0_1px_0_rgba(255,255,255,0.25)] transition-all duration-500",
          isHigh
            ? "from-[var(--danger)] to-[#ff7a80]"
            : isMedium
              ? "from-[var(--warning)] to-[#ffc457]"
              : "from-[var(--accent)] to-[var(--accent-2)]",
        )}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
