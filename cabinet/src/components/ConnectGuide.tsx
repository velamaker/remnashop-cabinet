import { useMemo, useState } from "react";
import { Download, Zap, Check, Star } from "lucide-react";

type Platform = "ios" | "android" | "windows" | "macos" | "androidtv";

interface AppEntry {
  id: string;
  name: string;
  desc: string;
  recommended?: boolean;
  platforms: Platform[];
  /** Строит deep-link импорта подписки в приложение. */
  deepLink: (sub: string) => string;
  /** Ссылка установки на каждую платформу. */
  install: Partial<Record<Platform, string>>;
}

const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "ios", label: "iPhone / iPad" },
  { id: "android", label: "Android" },
  { id: "windows", label: "Windows" },
  { id: "macos", label: "macOS" },
  { id: "androidtv", label: "Android TV" },
];

const APPS: AppEntry[] = [
  {
    id: "happ",
    name: "Happ",
    desc: "Простое и быстрое — рекомендуем для большинства",
    recommended: true,
    platforms: ["ios", "android", "macos", "windows", "androidtv"],
    deepLink: (sub) => `happ://add/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6504287215",
      android: "https://play.google.com/store/apps/details?id=com.happproxy",
      macos: "https://apps.apple.com/app/id6504287215",
      windows: "https://github.com/Happ-proxy/happ-desktop/releases/latest",
      androidtv: "https://github.com/Happ-proxy/happ-android/releases/latest",
    },
  },
  {
    id: "v2raytun",
    name: "v2RayTun",
    desc: "Популярный кросс-платформенный клиент",
    platforms: ["ios", "android", "windows", "androidtv"],
    deepLink: (sub) => `v2raytun://import/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6476628951",
      android: "https://play.google.com/store/apps/details?id=com.v2raytun.android",
      windows: "https://v2raytun.com",
      androidtv: "https://play.google.com/store/apps/details?id=com.v2raytun.android",
    },
  },
  {
    id: "streisand",
    name: "Streisand",
    desc: "Лёгкий клиент для Apple",
    platforms: ["ios", "macos"],
    deepLink: (sub) => `streisand://import/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6450534064",
      macos: "https://apps.apple.com/app/id6450534064",
    },
  },
  {
    id: "hiddify",
    name: "Hiddify",
    desc: "Открытый клиент для всех платформ",
    platforms: ["android", "windows", "macos"],
    deepLink: (sub) => `hiddify://import/${sub}`,
    install: {
      android: "https://play.google.com/store/apps/details?id=app.hiddify.com",
      windows: "https://github.com/hiddify/hiddify-app/releases/latest",
      macos: "https://github.com/hiddify/hiddify-app/releases/latest",
    },
  },
];

function detectPlatform(): Platform {
  const ua = (navigator.userAgent || "").toLowerCase();
  if (/iphone|ipad|ipod/.test(ua)) return "ios";
  if (/android tv|googletv|smart-tv/.test(ua)) return "androidtv";
  if (/android/.test(ua)) return "android";
  if (/macintosh|mac os x/.test(ua)) return "macos";
  return "windows";
}

function AppCard({ app, platform, sub }: { app: AppEntry; platform: Platform; sub: string }) {
  const [connected, setConnected] = useState(false);
  const installUrl = app.install[platform];

  const handleConnect = () => {
    window.location.href = app.deepLink(sub);
    setConnected(true);
    setTimeout(() => setConnected(false), 2500);
  };

  return (
    <div className="surface flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-fg">{app.name}</span>
          {app.recommended && (
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

  const apps = useMemo(() => APPS.filter((a) => a.platforms.includes(platform)), [platform]);

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

      {/* Приложения */}
      <div className="mt-4 flex flex-col gap-2.5">
        {apps.map((app) => (
          <AppCard key={app.id} app={app} platform={platform} sub={subUrl} />
        ))}
        {apps.length === 0 && (
          <p className="py-4 text-center text-sm text-fg-subtle">
            Для этой платформы пока нет рекомендованных приложений
          </p>
        )}
      </div>
    </div>
  );
}
