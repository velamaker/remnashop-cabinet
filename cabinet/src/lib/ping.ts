import { useEffect, useRef, useState } from "react";

// Клиентский (браузерный) замер пинга до хоста ноды. Меряется С УСТРОЙСТВА
// пользователя — т.е. если пользователь в России, видит реальную latency из
// России (сервер бота в Польше мерить «из России» не может).
//
// Приём: грузим картинку с https://<host>/ и засекаем время до onload/onerror.
// Для VPN-ноды валидный ответ не придёт (cert/reality), но onerror срабатывает
// после установки соединения+TLS ≈ RTT. Не идеально точно, но честно отражает
// «ближе/дальше» с устройства юзера. Не ответившие в таймаут → null («—»).
export function pingHost(host: string, timeoutMs = 2500): Promise<number | null> {
  return new Promise((resolve) => {
    if (!host) return resolve(null);
    const start = performance.now();
    const img = new Image();
    let settled = false;
    const done = (ms: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      img.onload = null;
      img.onerror = null;
      resolve(ms);
    };
    const timer = setTimeout(() => done(null), timeoutMs);
    const finish = () => done(Math.max(1, Math.round(performance.now() - start)));
    img.onload = finish;
    img.onerror = finish; // ошибка (cert/404) тоже = соединение установлено ≈ RTT
    img.src = `https://${host}/favicon.ico?_=${Date.now()}`;
  });
}

// Лучшее из нескольких попыток (меньше джиттера).
async function pingBest(host: string, attempts = 2): Promise<number | null> {
  let best: number | null = null;
  for (let i = 0; i < attempts; i++) {
    const v = await pingHost(host);
    if (v != null && (best == null || v < best)) best = v;
  }
  return best;
}

// Хук: пингует хосты онлайн-нод из браузера, возвращает {host: ms|null}.
// Перемеряет при смене набора хостов и раз в 30 с.
export function useNodePings(nodes: { host: string; online: boolean }[]): Record<string, number | null> {
  const [pings, setPings] = useState<Record<string, number | null>>({});
  const key = nodes.map((n) => (n.online ? n.host : "")).join("|");
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    let alive = true;
    const run = async () => {
      const results = await Promise.all(
        nodes.map((n) => (n.host && n.online ? pingBest(n.host) : Promise.resolve(null))),
      );
      if (!alive) return;
      // Не затираем последнее удачное значение транзиентным null: на мобильных
      // (iOS Safari, переиспользование TLS-соединения) отдельный замер может
      // зависнуть до таймаута и вернуть null — тогда бейдж пинга мигал/пропадал
      // после нескольких обновлений. Держим last-good по хосту, обновляем только
      // при успешном замере; хосты, которых больше нет в наборе, отбрасываем.
      setPings((prev) => {
        const map: Record<string, number | null> = {};
        nodes.forEach((n, i) => {
          if (!n.host) return;
          const v = results[i] ?? null;
          map[n.host] = v != null ? v : prev[n.host] ?? null;
        });
        return map;
      });
    };
    run();
    const id = setInterval(run, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return pings;
}
