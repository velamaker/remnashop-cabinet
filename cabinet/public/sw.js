// Минимальный service worker кабинета — нужен для установки как PWA («на экран
// Домой») и офлайн-оболочки. Осознанно НЕ workbox/vite-plugin-pwa: полный контроль
// над стратегиями, чтобы НЕ отдавать устаревший index.html после деплоя.
//
// Стратегии:
//   • /api/*            → только сеть (никогда не кэшируем — динамика/приватка).
//   • навигация (HTML)  → network-first: свежий index.html при онлайне, кэш-фолбэк офлайн.
//   • статика /assets/… → cache-first (имена хешированы Vite → безопасно, immutable).
// Версию бампать при смене стратегий (старые кэши чистятся в activate).
const CACHE = "cabinet-shell-v1";
const SHELL = "/index.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.add(SHELL)).catch(() => {}),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // сторонние (flagcdn, telegram) — не трогаем
  if (url.pathname.startsWith("/api/")) return;     // API — только сеть, мимо SW

  // Навигация (открытие страницы) — network-first, кэш только как офлайн-фолбэк.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(SHELL, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(SHELL).then((r) => r || caches.match(req))),
    );
    return;
  }

  // Хешированная статика — cache-first (кладём в кэш по факту запроса).
  if (url.pathname.startsWith("/assets/") || /\.(?:png|svg|webmanifest|woff2?)$/.test(url.pathname)) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
            return res;
          }),
      ),
    );
  }
});

// ── Web Push ────────────────────────────────────────────────────────────────
// Приходит push от бэкенда (services/overlay_push.py) → показываем уведомление.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Уведомление";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.tag,
      data: { url: data.url || "/" },
    }),
  );
});

// Клик по уведомлению — фокус на открытую вкладку кабинета или открытие новой.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          if ("navigate" in client) client.navigate(target).catch(() => {});
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    }),
  );
});
