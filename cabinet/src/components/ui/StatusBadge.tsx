import { clsx } from "clsx";
import { useT } from "@/i18n/I18nContext";

const statusConfig: Record<
  string,
  { label: string; className: string; live?: boolean }
> = {
  ACTIVE: {
    label: "badge.active",
    className: "bg-success/10 text-success ring-1 ring-inset ring-success/20",
    live: true,
  },
  EXPIRED: {
    label: "badge.expired",
    className: "bg-danger/10 text-danger ring-1 ring-inset ring-danger/20",
  },
  DISABLED: {
    label: "badge.disabled",
    className: "bg-fg-subtle/10 text-fg-subtle ring-1 ring-inset ring-fg-subtle/20",
  },
};

export function StatusBadge({ status }: { status: string }) {
  const t = useT();
  const config = statusConfig[status] || {
    label: status,
    className: "bg-fg-subtle/10 text-fg-subtle ring-1 ring-inset ring-fg-subtle/20",
  };

  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        config.className,
      )}
    >
      <span className="relative flex h-1.5 w-1.5">
        {config.live && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
        )}
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {t(config.label)}
    </span>
  );
}
