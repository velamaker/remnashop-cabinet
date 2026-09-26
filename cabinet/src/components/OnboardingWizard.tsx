import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  Download,
  Loader2,
  RefreshCw,
  Rocket,
  Smartphone,
  Stethoscope,
  X,
} from "lucide-react";
import { subscriptionApi } from "@/api/subscription";
import { APPS, PLATFORMS, DEFAULT_PRIORITY, type Platform } from "@/data/apps";
import { useT } from "@/i18n/I18nContext";
import type { SubscriptionInfoResponse } from "@/types/api";

const DISMISS_KEY = "onboarding_done";
const STEPS = [1, 2, 3, 4] as const;

/** Как часто спрашиваем подписку, пока ждём первое подключение. */
export const CONNECTION_POLL_MS = 5_000;
/** Сколько ждём в целом, прежде чем честно сказать «пока не видим». */
export const CONNECTION_WAIT_MS = 120_000;

/**
 * Было ли у подписки хоть одно подключение. Признаки — те же, по которым судит
 * самодиагностика: последний онлайн из панели или любой расход трафика. Все три
 * поля живут только в Remnawave; нули — «ещё не подключался», продолжаем спрашивать.
 * Все три пустые — это «не знаем», а не «не подключался» (см. panelSilent).
 */
export function hasConnected(sub: SubscriptionInfoResponse | null | undefined): boolean {
  if (!sub) return false;
  return (
    !!sub.online_at ||
    (sub.lifetime_used_traffic_bytes ?? 0) > 0 ||
    (sub.used_traffic_bytes ?? 0) > 0
  );
}

/**
 * Подписка есть, а ни одного признака подключения в ответе нет: ни последнего
 * онлайна, ни расхода — даже нулевого. Так отвечает адаптер «Бедолаги» (этих полей
 * у него нет вовсе) и наш бэкенд, когда Remnawave не ответила. Правило то же, что в
 * самодиагностике (DiagnosticWizard): ПАНЕЛЬ МОЛЧИТ — НЕ ПОВОД ВЫНОСИТЬ ВЕРДИКТ.
 */
export function panelSilent(sub: SubscriptionInfoResponse | null | undefined): boolean {
  return (
    !!sub &&
    sub.online_at == null &&
    sub.used_traffic_bytes == null &&
    sub.lifetime_used_traffic_bytes == null
  );
}

type CheckState = "waiting" | "connected" | "already" | "timeout" | "unknown";

/**
 * Последний шаг мастера: кабинет сам ждёт первого подключения.
 *
 * ЗАЧЕМ. Из 200 пробных аккаунтов 169 не подключились ни разу. Мастер доводил
 * человека до кнопки «Добавить подписку» и отпускал со словом «Готово» — а что
 * VPN так и не заработал, человек узнавал уже без нас, когда пробный срок
 * кончался. Здесь кабинет каждые 5 секунд спрашивает подписку и либо говорит
 * «всё работает», либо через 2 минуты ведёт в самодиагностику, пока человек
 * ещё рядом и готов разбираться.
 *
 * Если в ответе нет ни одного признака подключения (panelSilent: адаптер чужого
 * бота или панель не ответила), вердикта нет: шаг сразу завершается нейтральным
 * «Готово», как было до этой проверки, без «пока не видим подключения».
 *
 * Опрос — цепочкой setTimeout, а не setInterval: медленный ответ панели не
 * накладывается на следующий запрос. При размонтировании (закрыли мастер, шаг
 * назад, ушли со страницы) таймер гасится, а ответ, долетевший после, молча
 * выбрасывается — иначе опрос продолжал бы ходить в панель с чужой страницы.
 */
export function ConnectionCheck({ onDone }: { onDone: () => void }) {
  const t = useT();
  const [state, setState] = useState<CheckState>("waiting");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Номер текущего прохода: ответ от прошлого прохода (или после размонтирования)
  // не должен менять экран.
  const run = useRef(0);

  const stop = useCallback(() => {
    run.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const start = useCallback(
    (initial: boolean) => {
      stop();
      const id = run.current;
      const attempts = Math.ceil(CONNECTION_WAIT_MS / CONNECTION_POLL_MS);
      setState("waiting");

      const tick = async (n: number) => {
        let sub: SubscriptionInfoResponse | null = null;
        try {
          sub = await subscriptionApi.current();
        } catch {
          /* сбой сети или панели — это не «не подключился», спросим ещё раз */
        }
        if (id !== run.current) return;
        if (hasConnected(sub)) {
          // Подключение видно с первого же ответа — значит, оно было и раньше:
          // ждать нечего, человеку незачем смотреть на крутилку.
          setState(initial && n === 0 ? "already" : "connected");
          return;
        }
        if (panelSilent(sub)) {
          // Данных о подключении нет вовсе: ждать нечего — заметить подключение
          // здесь всё равно не выйдет, а «пока не видим» было бы уверенной неправдой.
          // Шаг завершается нейтральным «Готово», как до появления проверки.
          setState("unknown");
          return;
        }
        if (n >= attempts) {
          setState("timeout");
          return;
        }
        timer.current = setTimeout(() => void tick(n + 1), CONNECTION_POLL_MS);
      };

      void tick(0);
    },
    [stop],
  );

  useEffect(() => {
    start(true);
    return stop;
  }, [start, stop]);

  if (state === "connected" || state === "already") {
    return (
      <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-3">
        <p className="flex items-center gap-2 text-sm font-semibold text-fg">
          <CheckCircle2 className="h-5 w-5 flex-shrink-0 text-success" />
          {t(state === "already" ? "onb.check.already" : "onb.check.ok")}
        </p>
        <p className="mt-1 text-xs text-fg-muted">
          {t(state === "already" ? "onb.check.alreadyHint" : "onb.check.okHint")}
        </p>
        {state === "already" && (
          <Link
            to="/support"
            className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
          >
            <Stethoscope className="h-3.5 w-3.5" /> {t("onb.check.toDiag")}
          </Link>
        )}
        <div className="mt-3 flex justify-end">
          <button type="button" onClick={onDone} className="inline-flex items-center gap-1 text-sm font-semibold text-success">
            <Check className="h-4 w-4" /> {t("onb.done")}
          </button>
        </div>
      </div>
    );
  }

  if (state === "unknown") {
    return (
      <div className="rounded-xl border border-border-subtle bg-bg p-3">
        <p className="text-xs text-fg-muted">{t("onb.check.unknown")}</p>
        <div className="mt-3 flex justify-end">
          <button type="button" onClick={onDone} className="inline-flex items-center gap-1 text-sm font-semibold text-success">
            <Check className="h-4 w-4" /> {t("onb.done")}
          </button>
        </div>
      </div>
    );
  }

  if (state === "timeout") {
    return (
      <div className="rounded-xl border border-amber-400/40 bg-amber-400/10 p-3">
        <p className="flex items-center gap-2 text-sm font-semibold text-fg">
          <AlertTriangle className="h-5 w-5 flex-shrink-0 text-amber-500" />
          {t("onb.check.notYet")}
        </p>
        <p className="mt-1 text-xs text-fg-muted">{t("onb.check.notYetHint")}</p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <Link
            to="/support"
            className="btn-gradient inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold text-white"
          >
            <Stethoscope className="h-4 w-4" /> {t("onb.check.toDiag")}
          </Link>
          <button
            type="button"
            onClick={() => start(false)}
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-border-subtle bg-bg px-4 py-2 text-sm font-medium text-fg hover:bg-bg-raised"
          >
            <RefreshCw className="h-4 w-4" /> {t("onb.check.again")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-border-subtle bg-bg p-3" role="status">
      <Loader2 className="mt-0.5 h-4 w-4 flex-shrink-0 animate-spin text-accent" />
      <div>
        <p className="text-sm font-medium text-fg">{t("onb.check.waiting")}</p>
        <p className="mt-0.5 text-xs text-fg-muted">{t("onb.check.waitingHint")}</p>
      </div>
    </div>
  );
}

/**
 * Онбординг-визард для новичка: 4 шага — выбери устройство → установи приложение →
 * добавь подписку → проверим, что заработало. Поверх тех же APPS, но упрощённо
 * (рекомендованное приложение). Скрывается после «Готово» (localStorage), можно
 * закрыть крестиком.
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
        {STEPS.map((n) => (
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
            <p className="mt-0.5 text-xs text-fg-muted">{t(app.desc)}</p>
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
            <button
              type="button"
              onClick={() => setStep(4)}
              className="inline-flex items-center gap-1 text-sm font-semibold text-accent"
            >
              {t("onb.addedNext")} <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {/* Шаг 4 — проверка, что VPN действительно заработал */}
      {step === 4 && app && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-fg">{t("onb.step4")}</p>
          <ConnectionCheck onDone={close} />
          <div className="mt-3">
            <button type="button" onClick={() => setStep(3)} className="text-sm text-fg-muted hover:text-fg">
              {t("onb.back")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
