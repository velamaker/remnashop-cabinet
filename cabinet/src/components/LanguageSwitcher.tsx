import { useEffect, useMemo, useRef, useState } from "react";
import { Globe, Check } from "lucide-react";
import { LANGUAGES, enabledLanguages } from "@/i18n/config";
import { useI18n, useT } from "@/i18n/I18nContext";
import { useBranding } from "@/contexts/BrandingContext";

/** Флаг картинкой (emoji-флаги не рендерятся на Windows). */
function Flag({ country, className }: { country: string; className?: string }) {
  return (
    <img
      src={`https://flagcdn.com/h24/${country}.png`}
      srcSet={`https://flagcdn.com/h48/${country}.png 2x`}
      alt=""
      loading="lazy"
      className={className ?? "h-3.5 w-5 rounded-[2px] object-cover shadow-sm"}
    />
  );
}

/** Селектор языка — инлайн (ставится в шапку рядом с переключателем темы).
 * Дропдаун открывается вниз-вправо. Выбор запоминается (localStorage). */
export function LanguageSwitcher() {
  const { lang, setLang } = useI18n();
  const { appearance } = useBranding();
  const languages = useMemo(
    () => enabledLanguages(appearance?.enabled_languages),
    [appearance?.enabled_languages],
  );
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const t = useT();
  const current = LANGUAGES.find((l) => l.code === lang) ?? LANGUAGES[0]!;

  // Один язык — переключать нечего, прячем селектор.
  if (languages.length <= 1) return null;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={t("common.selectLang")}
        className="flex h-9 items-center gap-1.5 rounded-xl border border-border-subtle bg-bg-subtle px-2.5 text-sm font-medium text-fg transition-colors hover:bg-bg-overlay"
      >
        <Globe className="h-4 w-4 text-fg-muted" />
        <Flag country={current.country} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 max-h-[60vh] w-52 overflow-y-auto rounded-2xl border border-border-subtle bg-bg-raised p-1.5 shadow-xl">
          {languages.map((l) => (
            <button
              key={l.code}
              onClick={() => {
                setLang(l.code);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm transition-colors ${
                l.code === lang ? "bg-accent-subtle text-accent" : "text-fg hover:bg-bg-overlay"
              }`}
            >
              <Flag country={l.country} className="h-4 w-6 rounded-[2px] object-cover shadow-sm" />
              <span className="flex-1">{l.label}</span>
              {l.code === lang && <Check className="h-4 w-4" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
