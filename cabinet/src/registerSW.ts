// Регистрация service worker для PWA (установка «на экран Домой» + офлайн-оболочка).
// Только в проде: в dev (vite) SW мешал бы HMR и кэшировал бы бандл.
// SW сам по себе network-first для навигации — устаревший index.html не отдаётся.
export function registerServiceWorker() {
  if (!import.meta.env.PROD) return;
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* установка PWA просто не будет доступна — не критично */
    });
  });
}
