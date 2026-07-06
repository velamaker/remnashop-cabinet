import { type HTMLAttributes, forwardRef } from "react";
import { clsx } from "clsx";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: "raised" | "subtle" | "bordered";
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = "raised", ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={clsx(
          "rounded-xl p-5 transition-[border-color,box-shadow,transform] duration-200",
          variant === "raised" && "border border-[var(--border)] bg-bg-raised",
          variant === "subtle" && "bg-bg-subtle",
          variant === "bordered" && "border border-[var(--border)] bg-transparent",
          className,
        )}
        {...props}
      />
    );
  },
);
Card.displayName = "Card";

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h3 className="text-sm font-semibold tracking-tight text-fg">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-fg-subtle">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
