import { useState } from "react";
import { Gauge, Loader2, RefreshCw, ArrowDown, ArrowUp } from "lucide-react";
import { useT } from "@/i18n/I18nContext";

// Клиентский замер скорости через публичные эндпоинты Cloudflare.
// Меряет ТЕКУЩЕЕ соединение браузера до Cloudflare — под VPN это скорость туннеля.
// Замер многопоточный и time-boxed (как `iperf -P 8`): один поток на лоссовом/
// международном канале режет скорость вдвое и врёт в меньшую сторону.
const DOWN_URL = "https://speed.cloudflare.com/__down?bytes=";
const UP_URL = "https://speed.cloudflare.com/__up";
const STREAMS = 6; // параллельных соединений
const PHASE_MS = 5000; // длительность каждой фазы (скачивание / отдача)
const DOWN_CHUNK = 25_000_000; // байт на один запрос скачивания
const UP_CHUNK = 2_000_000; // байт на один запрос отдачи

/** Скачивание: качаем параллельными потоками до дедлайна, считаем реально принятые байты. */
async function measureDown(): Promise<number> {
  const start = performance.now();
  const deadline = start + PHASE_MS;
  let total = 0;

  const worker = async () => {
    while (performance.now() < deadline) {
      const res = await fetch(`${DOWN_URL}${DOWN_CHUNK}&t=${Date.now()}${Math.random()}`, { cache: "no-store" });
      const reader = res.body?.getReader();
      if (!reader) {
        total += (await res.arrayBuffer()).byteLength;
        continue;
      }
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (performance.now() >= deadline) {
          await reader.cancel();
          break;
        }
      }
    }
  };

  await Promise.all(Array.from({ length: STREAMS }, worker));
  const secs = (performance.now() - start) / 1000;
  return secs > 0 ? (total * 8) / secs / 1e6 : 0;
}

/** Отдача: шлём параллельными POST до дедлайна, считаем отправленные байты. */
async function measureUp(): Promise<number> {
  const payload = new Uint8Array(UP_CHUNK); // нули жмутся, но CF не сжимает __up
  const start = performance.now();
  const deadline = start + PHASE_MS;
  let total = 0;

  const worker = async () => {
    while (performance.now() < deadline) {
      await fetch(UP_URL, { method: "POST", body: payload, cache: "no-store" });
      total += UP_CHUNK;
    }
  };

  await Promise.all(Array.from({ length: STREAMS }, worker));
  const secs = (performance.now() - start) / 1000;
  return secs > 0 ? (total * 8) / secs / 1e6 : 0;
}

type Phase = "idle" | "down" | "up" | "done" | "err";

function tone(mbps: number | null): string {
  if (mbps == null) return "text-fg";
  return mbps >= 100 ? "text-success" : mbps >= 30 ? "text-amber-500" : "text-danger";
}

export function SpeedtestWidget() {
  const t = useT();
  const [phase, setPhase] = useState<Phase>("idle");
  const [down, setDown] = useState<number | null>(null);
  const [up, setUp] = useState<number | null>(null);

  const run = async () => {
    setDown(null);
    setUp(null);
    try {
      setPhase("down");
      const d = await measureDown();
      setDown(d);
      setPhase("up");
      const u = await measureUp();
      setUp(u);
      setPhase("done");
    } catch {
      setPhase("err");
    }
  };

  const running = phase === "down" || phase === "up";

  return (
    <div className="rounded-2xl border border-border-subtle bg-bg-subtle p-4 sm:p-5">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent">
          <Gauge className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-bold text-fg">{t("speed.title")}</p>
          <p className="mt-0.5 text-xs text-fg-muted">
            {phase === "done" && (down != null || up != null) ? (
              <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-0.5">
                <span className={`inline-flex items-center gap-1 text-sm font-semibold ${tone(down)}`}>
                  <ArrowDown className="h-3.5 w-3.5" />
                  {down != null ? down.toFixed(0) : "—"}
                </span>
                <span className={`inline-flex items-center gap-1 text-sm font-semibold ${tone(up)}`}>
                  <ArrowUp className="h-3.5 w-3.5" />
                  {up != null ? up.toFixed(0) : "—"}
                </span>
                <span className="text-xs text-fg-subtle">{t("speed.unit")}</span>
              </span>
            ) : phase === "err" ? (
              <span className="text-danger">{t("speed.err")}</span>
            ) : phase === "down" ? (
              t("speed.down")
            ) : phase === "up" ? (
              t("speed.up")
            ) : (
              t("speed.idle")
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={run}
          disabled={running}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-accent-fg hover:bg-accent/90 disabled:opacity-50"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : phase === "done" ? <RefreshCw className="h-4 w-4" /> : <Gauge className="h-4 w-4" />}
          {phase === "done" ? t("speed.again") : t("speed.check")}
        </button>
      </div>
    </div>
  );
}
