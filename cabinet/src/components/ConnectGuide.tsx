import { useEffect, useMemo, useState } from "react";
import { Download, Zap, Check, Star, QrCode, X, Link2, Copy } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { APPS, PLATFORMS, DEFAULT_PRIORITY, type AppEntry, type Platform } from "@/data/apps";
import { appsApi, type AppsConfig } from "@/api/apps";
import { openExternalLink, useIsMiniApp } from "@/hooks/useTelegramWebApp";

function detectPlatform(): Platform {
  const ua = (navigator.userAgent || "").toLowerCase();
  if (/iphone|ipad|ipod/.test(ua)) return "ios";
  if (/android tv|googletv|smart-tv/.test(ua)) return "androidtv";
  if (/android/.test(ua)) return "android";
  if (/macintosh|mac os x/.test(ua)) return "macos";
  return "windows";
}

function AppCard({
  app,
  platform,
  sub,
  recommended,
}: {
  app: AppEntry;
  platform: Platform;
  sub: string;
  recommended?: boolean;
}) {
  const [connected, setConnected] = useState(false);
  const installUrl = app.install[platform];

  const handleConnect = () => {
    openExternalLink(app.deepLink(sub));
    setConnected(true);
    setTimeout(() => setConnected(false), 2500);
  };

  return (
    <div className="surface flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-fg">{app.name}</span>
          {recommended && (
            <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent-subtle px-2 py-0.5 text-[10px] font-medium text-accent">
              <Star className="h-3 w-3" />
              Рекомендуем
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-fg-muted">{app.desc}</p>
      </div>
      <div className="flex flex-shrink-0 gap-2">
        {installUrl && (
          <a
            href={installUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] bg-bg-raised px-3 text-xs font-medium text-fg transition-colors hover:bg-bg-overlay"
          >
            <Download className="h-3.5 w-3.5" />
            Установить
          </a>
        )}
        <button
          onClick={handleConnect}
          className="btn-gradient inline-flex h-9 items-center justify-center gap-1.5 rounded-lg px-4 text-xs font-semibold transition-all active:scale-[0.98]"
        >
          {connected ? <Check className="h-3.5 w-3.5" /> : <Zap className="h-3.5 w-3.5" />}
          {connected ? "Открываем…" : "Подключиться"}
        </button>
      </div>
    </div>
  );
}

export function ConnectGuide({ subUrl }: { subUrl: string }) {
  const [platform, setPlatform] = useState<Platform>(detectPlatform);
  const [showQr, setShowQr] = useState(false);
  const [copied, setCopied] = useState(false);
  // Telegram Mini App не открывает кастомные схемы (happ://, hiddify:// и т.п.) —
  // это ограничение самого Telegram, кнопка «Подключиться» там может не сработать.
  // Подсказываем сразу, куда смотреть, а не оставляем гадать после неудачного клика.
  const isMiniApp = useIsMiniApp();

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(subUrl);
    } catch {
      // clipboard недоступен (http/старый браузер) — выделяем через prompt-фолбэк
      window.prompt("Скопируйте ссылку подписки:", subUrl);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  // Выбор админа: какие приложения показывать и какое приоритетное.
  const [config, setConfig] = useState<AppsConfig | null>(null);

  useEffect(() => {
    appsApi
      .get()
      .then(setConfig)
      .catch(() => setConfig(null)); // при ошибке — показываем все (дефолт)
  }, []);

  const priority = config?.priority || DEFAULT_PRIORITY;

  const apps = useMemo(() => {
    // 1) встроенные приложения под выбранную платформу
    let list = APPS.filter((a) => a.platforms.includes(platform));
    // 2) если админ ограничил список — оставляем только включённые
    if (config?.enabled) {
      const allow = new Set(config.enabled);
      list = list.filter((a) => allow.has(a.id));
    }
    // 3) свои приложения админа (всегда показываем; deep_link — шаблон с {sub})
    const custom: AppEntry[] = (config?.custom ?? [])
      .filter((c) => (c.platforms as Platform[]).includes(platform))
      .map((c) => {
        const iu = c.install_url;
        return {
          id: c.id,
          name: c.name,
          desc: c.desc,
          platforms: c.platforms as Platform[],
          deepLink: (sub: string) => c.deep_link.replace("{sub}", sub),
          install: iu
            ? (Object.fromEntries(c.platforms.map((p) => [p, iu])) as Partial<Record<Platform, string>>)
            : {},
        };
      });
    // 4) приоритетное приложение — первым
    return [...list, ...custom].sort((a, b) => {
      if (a.id === priority) return -1;
      if (b.id === priority) return 1;
      return 0;
    });
  }, [platform, config, priority]);

  return (
    <div className="surface p-5">
      <div className="mb-1">
        <h3 className="text-sm font-semibold tracking-tight text-fg">Подключить устройство</h3>
        <p className="mt-0.5 text-xs text-fg-subtle">
          Установите приложение и нажмите «Подключиться» — подписка добавится автоматически
        </p>
      </div>

      {/* Выбор платформы */}
      <div className="mt-4 flex flex-wrap gap-1.5">
        {PLATFORMS.map((p) => (
          <button
            key={p.id}
            onClick={() => setPlatform(p.id)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              platform === p.id
                ? "bg-accent text-accent-fg"
                : "border border-[var(--border)] bg-bg-raised text-fg-muted hover:text-fg"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {isMiniApp && (
        <p className="mt-3 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-fg">
          Открыто внутри Telegram — если кнопка «Подключиться» не переключает в
          приложение, скопируйте ссылку внизу страницы и вставьте её в приложении
          вручную («Добавить подписку по URL»).
        </p>
      )}

      {/* Приложения */}
      <div className="mt-4 flex flex-col gap-2.5">
        {apps.map((app) => (
          <AppCard
            key={app.id}
            app={app}
            platform={platform}
            sub={subUrl}
            recommended={app.id === priority}
          />
        ))}
        {apps.length === 0 && (
          <p className="py-4 text-center text-sm text-fg-subtle">
            Для этой платформы пока нет рекомендованных приложений
          </p>
        )}
      </div>

      {/* QR — подключить другое устройство (ТВ, второй телефон): отсканировать
          камерой приложения. Генерируется локально (qrcode.react), подписка
          наружу не уходит. */}
      <div className="mt-4 border-t border-[var(--border)] pt-4">
        <button
          type="button"
          onClick={() => setShowQr((v) => !v)}
          className="inline-flex items-center gap-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg"
        >
          {showQr ? <X className="h-4 w-4" /> : <QrCode className="h-4 w-4" />}
          {showQr ? "Скрыть QR-код" : "QR для другого устройства"}
        </button>
        {showQr && (
          <div className="mt-3 flex flex-col items-center gap-2">
            <div className="rounded-2xl bg-white p-3">
              <QRCodeSVG value={subUrl} size={180} />
            </div>
            <p className="max-w-xs text-center text-xs text-fg-subtle">
              Отсканируйте камерой приложения на другом устройстве, чтобы добавить подписку
            </p>
          </div>
        )}
      </div>

      {/* Прямая ссылка подписки — вставить вручную в приложение («Добавить по URL»)
          или скопировать на другое устройство. */}
      <div className="mt-4 border-t border-[var(--border)] pt-4">
        <p className="flex items-center gap-2 text-sm font-medium text-fg">
          <Link2 className="h-4 w-4 text-fg-muted" />
          Прямая ссылка подписки
        </p>
        <p className="mt-0.5 text-xs text-fg-subtle">
          Если приложение просит ввести ссылку вручную — скопируйте и вставьте её в
          «Добавить подписку по URL».
        </p>
        <div className="mt-2 flex items-stretch gap-2">
          <input
            readOnly
            value={subUrl}
            onFocus={(e) => e.currentTarget.select()}
            className="min-w-0 flex-1 truncate rounded-lg border border-[var(--border)] bg-bg-subtle px-2.5 py-2 text-xs text-fg-muted outline-none"
          />
          <button
            type="button"
            onClick={copyLink}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-accent-fg transition-opacity hover:opacity-90"
          >
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            {copied ? "Скопировано" : "Копировать"}
          </button>
        </div>
      </div>
    </div>
  );
}
