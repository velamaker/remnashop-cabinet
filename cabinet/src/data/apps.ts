// Каталог клиентских приложений для подключения подписки.
// Какие из них показывать и какое «приоритетное» — выбирает админ
// (см. AdminAppsPage + бэкенд /apps). Здесь — полный справочник с deep-link'ами.
//
// Примечание: схемы импорта у приложений отличаются и иногда меняются между
// версиями. Если какой-то deep-link перестал работать — поправьте здесь. Для
// query-стиля ссылку подписки кодируем (encodeURIComponent), для Shadowrocket —
// base64. Во всех карточках также есть кнопка «Установить» и общий QR.
//
// Happ — отдельный случай: вместо обычной happ://add/<url> используем формат
// happ://crypt4/<...> (RSA-4096 шифрование ссылки публичным ключом Happ,
// @kastov/cryptohapp). Он прячет реальный домен подписки от DPI — Роскомнадзор
// блокирует sub-домены по паттерну, а зашифрованную ссылку разбирает только
// сам Happ. Если шифрование не удалось (например, слишком длинная ссылка —
// лимит PKCS1 ~500 байт) — откатываемся на обычный happ://add/.
import { createHappCryptoLink } from "@kastov/cryptohapp";

function happDeepLink(sub: string): string {
  try {
    const encrypted = createHappCryptoLink(sub, "v4", true);
    if (encrypted) return encrypted;
  } catch {
    // Шифрование недоступно/упало (напр. слишком длинная ссылка) — откат ниже.
  }
  return `happ://add/${sub}`;
}

export type Platform = "ios" | "android" | "windows" | "macos" | "androidtv";

export interface AppEntry {
  id: string;
  name: string;
  desc: string;
  platforms: Platform[];
  /** Строит deep-link импорта подписки в приложение. */
  deepLink: (sub: string) => string;
  /** Ссылка установки на каждую платформу. */
  install: Partial<Record<Platform, string>>;
}

export const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "ios", label: "iPhone / iPad" },
  { id: "android", label: "Android" },
  { id: "windows", label: "Windows" },
  { id: "macos", label: "macOS" },
  { id: "androidtv", label: "Android TV" },
];

const enc = (sub: string) => encodeURIComponent(sub);
const b64 = (sub: string) =>
  typeof btoa === "function" ? btoa(sub) : sub; // Shadowrocket sub://base64

/** id приоритетного приложения по умолчанию (если админ не выбрал своё). */
export const DEFAULT_PRIORITY = "happ";

export const APPS: AppEntry[] = [
  {
    id: "happ",
    name: "Happ",
    desc: "app.happ.desc",
    platforms: ["ios", "android", "macos", "windows", "androidtv"],
    deepLink: happDeepLink,
    install: {
      ios: "https://apps.apple.com/app/id6504287215",
      android: "https://play.google.com/store/apps/details?id=com.happproxy",
      macos: "https://apps.apple.com/app/id6504287215",
      windows: "https://github.com/Happ-proxy/happ-desktop/releases/latest",
      androidtv: "https://github.com/Happ-proxy/happ-android/releases/latest",
    },
  },
  {
    id: "incy",
    name: "INCY",
    desc: "app.incy.desc",
    platforms: ["ios", "macos", "android"],
    deepLink: (sub) => `incy://import/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6756943388",
      macos: "https://apps.apple.com/app/id6756943388",
      android: "https://play.google.com/store/apps/details?id=llc.itdev.incy",
    },
  },
  {
    id: "v2raytun",
    name: "v2RayTun",
    desc: "app.v2raytun.desc",
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
    id: "v2rayng",
    name: "v2rayNG",
    desc: "app.v2rayng.desc",
    platforms: ["android", "androidtv"],
    deepLink: (sub) => `v2rayng://install-sub?url=${enc(sub)}`,
    install: {
      android: "https://play.google.com/store/apps/details?id=com.v2ray.ang",
      androidtv: "https://github.com/2dust/v2rayNG/releases/latest",
    },
  },
  {
    id: "hiddify",
    name: "Hiddify",
    desc: "app.hiddify.desc",
    platforms: ["ios", "android", "windows", "macos", "androidtv"],
    deepLink: (sub) => `hiddify://import/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6596777532",
      android: "https://play.google.com/store/apps/details?id=app.hiddify.com",
      windows: "https://github.com/hiddify/hiddify-app/releases/latest",
      macos: "https://github.com/hiddify/hiddify-app/releases/latest",
      androidtv: "https://github.com/hiddify/hiddify-app/releases/latest",
    },
  },
  {
    id: "streisand",
    name: "Streisand",
    desc: "app.streisand.desc",
    platforms: ["ios", "macos"],
    deepLink: (sub) => `streisand://import/${sub}`,
    install: {
      ios: "https://apps.apple.com/app/id6450534064",
      macos: "https://apps.apple.com/app/id6450534064",
    },
  },
  {
    id: "shadowrocket",
    name: "Shadowrocket",
    desc: "app.shadowrocket.desc",
    platforms: ["ios", "macos"],
    deepLink: (sub) => `sub://${b64(sub)}`,
    install: {
      ios: "https://apps.apple.com/app/id932747118",
      macos: "https://apps.apple.com/app/id932747118",
    },
  },
  {
    id: "karing",
    name: "Karing",
    desc: "app.karing.desc",
    platforms: ["ios", "android", "windows", "macos", "androidtv"],
    deepLink: (sub) => `karing://install-config?url=${enc(sub)}`,
    install: {
      ios: "https://apps.apple.com/app/id6472431552",
      android: "https://github.com/KaringX/karing/releases/latest",
      windows: "https://github.com/KaringX/karing/releases/latest",
      macos: "https://github.com/KaringX/karing/releases/latest",
      androidtv: "https://github.com/KaringX/karing/releases/latest",
    },
  },
  {
    id: "nekobox",
    name: "NekoBox",
    desc: "app.nekobox.desc",
    platforms: ["android"],
    deepLink: (sub) => `sing-box://import-remote-profile?url=${enc(sub)}`,
    install: {
      android: "https://github.com/MatsuriDayo/NekoBoxForAndroid/releases/latest",
    },
  },
  {
    id: "clash",
    name: "Clash Meta",
    desc: "app.clash.desc",
    platforms: ["windows", "macos", "android"],
    deepLink: (sub) => `clash://install-config?url=${enc(sub)}`,
    install: {
      windows: "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
      macos: "https://github.com/clash-verge-rev/clash-verge-rev/releases/latest",
      android: "https://github.com/chen08209/FlClash/releases/latest",
    },
  },
];
