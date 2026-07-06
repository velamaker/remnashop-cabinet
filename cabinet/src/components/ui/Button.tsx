import { forwardRef, type ButtonHTMLAttributes } from "react";
import { clsx } from "clsx";
import { Loader2 } from "lucide-react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
}

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-white shadow-[0_8px_20px_-8px_var(--accent-glow)] hover:brightness-[1.06] hover:shadow-[0_12px_28px_-6px_var(--accent-glow)] disabled:opacity-50 disabled:shadow-none",
  secondary:
    "bg-bg-raised text-fg border border-[var(--border)] hover:border-[var(--accent)] hover:bg-bg-overlay hover:shadow-[0_4px_16px_-8px_var(--accent-glow)] disabled:opacity-50",
  ghost:
    "bg-transparent text-fg-muted hover:bg-bg-raised hover:text-fg disabled:opacity-40",
  danger:
    "bg-danger/8 text-danger border border-danger/15 hover:bg-danger/12 disabled:opacity-50",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-7 px-3 text-xs rounded-md gap-1.5",
  md: "h-9 px-4 text-sm rounded-lg gap-2",
  lg: "h-10 px-5 text-sm rounded-lg gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "primary",
      size = "md",
      isLoading,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={clsx(
          "inline-flex items-center justify-center font-medium transition-all duration-150",
          "active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...props}
      >
        {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";
