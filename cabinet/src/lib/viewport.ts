/**
 * Фикс «белого экрана» в Telegram Mini App на Android при сворачивании/разворачивании.
 *
 * Симптом: свернул мини-апп, снова открыл — контент пропал (белый/пустой экран),
 * видны только закреплённые (fixed) шапка и нижнее меню.
 *
 * Причины (обе бьют по Android WebView):
 *  1) Telegram при возврате из фона может отдать высоту вьюпорта 0/устаревшую.
 *     Раньше высота #root завязана на var(--tg-viewport-stable-height) → при 0
 *     контейнер с overflow:hidden схлопывался, и контент исчезал.
 *  2) Сам WebView иногда не перерисовывается после resume, оставляя белый кадр.
 *
 * Лечение: держим свою CSS-переменную --app-height с защитой от нулевых значений
 * (см. #root в index.css), пересчитываем её на всех событиях резюма и форсим
 * перерисовку дерева + повторный expand() Telegram.
 */

interface TgViewport {
  viewportStableHeight?: number;
  viewportHeight?: number;
  expand?: () => void;
  onEvent?: (event: string, fn: () => void) => void;
}

function tg(): TgViewport | null {
  const w = window as unknown as { Telegram?: { WebApp?: TgViewport } };
  return w.Telegram?.WebApp ?? null;
}

/** Пишем реальную высоту вьюпорта в --app-height, отсекая нулевые/битые значения. */
function applyHeight(): void {
  const wa = tg();
  let tgh = 0;
  if (wa) tgh = Number(wa.viewportStableHeight) || Number(wa.viewportHeight) || 0;
  // window.innerHeight в WebView = высота области мини-аппа, всегда осмысленна.
  // Берём максимум: Telegram при резюме на Android отдаёт 0/устаревшее заниженное
  // значение → #root не должен схлопываться ниже реального размера WebView.
  const inner = window.innerHeight || 0;
  const h = Math.max(tgh, inner);
  if (h > 0) document.documentElement.style.setProperty("--app-height", `${h}px`);
}

let repainting = false;
/** Заставляем Android WebView перерисовать всё дерево (иначе — белый кадр). */
function forceRepaint(): void {
  if (repainting) return;
  const root = document.getElementById("root");
  if (!root) return;
  repainting = true;
  root.style.transform = "translateZ(0)";
  requestAnimationFrame(() => {
    root.style.transform = "";
    repainting = false;
  });
}

/** Возврат из фона: повторный expand + пересчёт высоты (с запасом по времени) + репейнт. */
function onResume(): void {
  const wa = tg();
  try {
    wa?.expand?.();
  } catch {
    /* expand не критичен */
  }
  applyHeight();
  // Высота после резюма может прийти с задержкой — обновим ещё пару раз.
  window.setTimeout(applyHeight, 120);
  window.setTimeout(applyHeight, 400);
  forceRepaint();
}

let started = false;

export function initViewport(): void {
  if (started) return;
  started = true;

  applyHeight();

  window.addEventListener("resize", applyHeight);
  window.addEventListener("orientationchange", onResume);
  window.addEventListener("focus", onResume);
  // pageshow ловит возврат из bfcache; visibilitychange — сворачивание/разворачивание.
  window.addEventListener("pageshow", onResume);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) onResume();
  });

  // Подписка на события Telegram-вьюпорта. SDK грузится асинхронно (см. index.html),
  // поэтому если он ещё не готов — ждём событие tg-webapp-settled.
  const hookTg = () => {
    try {
      tg()?.onEvent?.("viewportChanged", applyHeight);
    } catch {
      /* нет SDK — не критично */
    }
    applyHeight();
  };
  const w = window as unknown as { __tgWebAppExpected?: boolean; __tgWebAppSettled?: boolean };
  if (w.__tgWebAppExpected && !w.__tgWebAppSettled) {
    window.addEventListener("tg-webapp-settled", hookTg, { once: true });
  } else {
    hookTg();
  }
}
