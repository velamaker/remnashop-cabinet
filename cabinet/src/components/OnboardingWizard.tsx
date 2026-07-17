import { useMemo, useState } from "react";
import { Check, Download, ArrowRight, Copy, Smartphone, Rocket, X } from "lucide-react";
import { APPS, PLATFORMS, DEFAULT_PRIORITY, type Platform } from "@/data/apps";
import { useT } from "@/i18n/I18nContext";

const DISMISS_KEY = "onboarding_done";

/**
 * Онбординг-визард для новичка: 3 шага — выбери устройство → установи приложение →
 * подключись. Поверх тех же APPS, но упрощённо (рекомендованное приложение). Скрывается
 * после «Готово» (localStorage), можно закрыть крестиком.
 */
export function OnboardingWizard({ subUrl }: { subUrl: string }) {
  const t = useT();
  const [hidden, setHidden] = useState(() => localStorage.getItem(DISMISS_KEY) === "1");
  const [step, setStep] = useState(1);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [copied, setCopied] = useState(false);

  // Рекомендованное приложение для платформы. На iOS ряд клиентов снят из
  // российского App Store — рекомендуем доступный (incy); иначе happ.
  const app = useMemo(() => {
    if (!platform) return null;
    const forPlatform = APPS.filter((a) => a.platforms.includes(platform));
    const preferred = platform === "ios" ? "incy" : DEFAULT_PRIORITY;
    return (
      forPlatform.find((a) => a.id === preferred) ||
      forPlatform.find((a) => a.id === DEFAULT_PRIORITY) ||
      forPlatform[0] ||
      null
    );
  }, [platform]);

  if (hidden || !subUrl) return null;

  const close = () => {
    localStorage.setItem(DISMISS_KEY, "1");
    setHidden(true);
  };
  const installUrl = app && platform ? app.install[platform] : undefined;
  const deepLink = app ? app.deepLink(subUrl) : "";

  const copySub = async () => {
    try {
      await navigator.clipboard.writeText(subUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-accent/40 bg-gradient-to-br from-accent/10 to-accent-2/10 p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Rocket className="h-5 w-5 text-accent" />
          <h3 className="text-base font-bold text-fg">{t("onb.title")}</h3>
        </div>
        <button type="button" onClick={close} aria-label={t("onb.hide")} className="text-fg-subtle hover:text-fg">
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Шаги */}
      <div className="mt-3 flex items-center gap-1.5">
        {[1, 2, 3].map((n) => (
          <div
            key={n}
            className={`h-1.5 flex-1 rounded-full ${n <= step ? "bg-accent" : "bg-border-subtle"}`}
          />
        ))}
      </div>

      {/* Шаг 1 — устройство */}
      {step === 1 && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-fg">{t("onb.step1")}</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {PLATFORMS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setPlatform(p.id);
                  setStep(2);
                }}
                className="flex items-center gap-2 rounded-xl border border-border-subtle bg-bg px-3 py-2.5 text-sm text-fg hover:border-accent"
              >
                <Smartphone className="h-4 w-4 text-accent" /> {p.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Шаг 2 — приложение */}
      {step === 2 && app && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-fg">{t("onb.step2")}</p>
          <div className="rounded-xl border border-border-subtle bg-bg p-3">
            <p className="font-semibold text-fg">{app.name}</p>
            <p className="mt-0.5 text-xs text-fg-muted">{app.desc}</p>
            {installUrl && (
              <a
                href={installUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-flex items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-accent-fg hover:bg-accent/90"
              >
                <Download className="h-4 w-4" /> {t("onb.download", { app: app.name })}
              </a>
            )}
          </div>
          <div className="mt-3 flex justify-between">
            <button type="button" onClick={() => setStep(1)} className="text-sm text-fg-muted hover:text-fg">
              {t("onb.back")}
            </button>
            <button
              type="button"
              onClick={() => setStep(3)}
              className="inline-flex items-center gap-1 text-sm font-semibold text-accent"
            >
              {t("onb.installedNext")} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Шаг 3 — подключение */}
      {step === 3 && app && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-fg">{t("onb.step3")}</p>
          <p className="text-xs text-fg-muted">{t("onb.connectDesc", { app: app.name })}</p>
          <div className="mt-3 flex flex-col gap-2">
            <a
              href={deepLink}
              className="btn-gradient inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
            >
              {t("onb.addTo", { app: app.name })} <ArrowRight className="h-4 w-4" />
            </a>
            <button
              type="button"
              onClick={copySub}
              className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg hover:bg-bg-raised"
            >
              {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
              {copied ? t("onb.copied") : t("onb.copyLink")}
            </button>
          </div>
          <div className="mt-3 flex justify-between">
            <button type="button" onClick={() => setStep(2)} className="text-sm text-fg-muted hover:text-fg">
              {t("onb.back")}
            </button>
            <button type="button" onClick={close} className="inline-flex items-center gap-1 text-sm font-semibold text-success">
              <Check className="h-4 w-4" /> {t("onb.done")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
