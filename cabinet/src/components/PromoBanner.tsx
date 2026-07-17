import { useEffect, useState } from "react";
import { Megaphone, ArrowRight, X } from "lucide-react";
import { promoBannerApi, type PromoBannerStatus } from "@/api/promoBanner";
import { useT } from "@/i18n/I18nContext";

const COLOR: Record<string, string> = {
  accent: "border-accent/40 bg-gradient-to-br from-accent/15 to-accent-2/15 text-accent",
  red: "border-danger/40 bg-danger/10 text-danger",
  green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-500",
  amber: "border-amber-400/40 bg-amber-400/10 text-amber-500",
};

const DISMISS_KEY = "promo_banner_dismissed";

/**
 * Админ-настраиваемый промо-баннер. Сам ходит за статусом (/promo-banner) — бэкенд
 * учитывает тумблер, расписание и аудиторию. Скрывается по «version» (localStorage),
 * пока админ не сменит контент.
 */
export function PromoBanner() {
  const t = useT();
  const [data, setData] = useState<PromoBannerStatus | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let alive = true;
    promoBannerApi
      .get()
      .then((d) => {
        if (!alive) return;
        setData(d);
        if (d.active && d.dismissible && d.version) {
          setHidden(localStorage.getItem(DISMISS_KEY) === d.version);
        }
      })
      .catch(() => {
        /* тихо */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!data?.active || hidden) return null;

  const tone = COLOR[data.color || "accent"] || COLOR.accent;
  const dismiss = () => {
    if (data.version) localStorage.setItem(DISMISS_KEY, data.version);
    setHidden(true);
  };

  return (
    <div className={`overflow-hidden rounded-2xl border p-4 sm:p-5 ${tone}`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white/10">
          <Megaphone className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          {data.title && <p className="text-base font-bold text-fg">{data.title}</p>}
          {data.text && <p className="mt-0.5 whitespace-pre-line text-sm text-fg-muted">{data.text}</p>}
          {data.cta_text && data.cta_url && (
            <a
              href={data.cta_url}
              target={data.cta_url.startsWith("http") ? "_blank" : undefined}
              rel="noreferrer"
              className="mt-2.5 inline-flex items-center gap-1 rounded-xl bg-white/10 px-3 py-1.5 text-sm font-semibold"
            >
              {data.cta_text}
              <ArrowRight className="h-4 w-4" />
            </a>
          )}
        </div>
        {data.dismissible && (
          <button
            type="button"
            onClick={dismiss}
            aria-label={t("common.hide")}
            className="shrink-0 text-fg-subtle transition-colors hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
