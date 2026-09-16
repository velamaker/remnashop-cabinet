import type { DeviceResponse } from "@/types/api";

/**
 * «Несколько слотов занял один аппарат» — и как это распознать.
 *
 * ЗАЧЕМ. Лимит устройств считается по HWID, а HWID выдаёт КАЖДОЕ приложение
 * отдельно. Человек ставит на один телефон Happ и INCY, пробует оба — и два слота
 * из четырёх уже заняты. Со стороны это выглядит как «VPN рвётся»: новые
 * подключения упираются в лимит. Ровно такой разбор однажды занял переписку с
 * поддержкой, хотя человек мог решить всё сам за десять секунд.
 *
 * На боевых данных так живут несколько человек: два разных приложения на одном
 * айфоне или одно и то же приложение дважды — то есть переустановка оставила
 * мёртвый слот.
 *
 * ПОЧЕМУ ЭТО ПОДСКАЗКА, А НЕ ПРИГОВОР. Совпадение модели, платформы и версии ОС
 * не доказывает, что аппарат один: в семье бывают два одинаковых телефона с
 * одинаковой прошивкой. Поэтому экран не удаляет ничего сам и не утверждает — он
 * показывает, какие слоты похожи, и даёт решить человеку.
 */

/** Имя приложения из user-agent: «Happ/5.7.0/ios/…» → «Happ». */
export function appFromUserAgent(ua: string | null | undefined): string | null {
  const raw = (ua ?? "").trim();
  if (!raw) return null;
  // Панель пишет «Приложение/версия/платформа/…», но встречается и голое «xray».
  const name = (raw.split(/[/\s]/)[0] ?? "").trim();
  return name || null;
}

/**
 * Ключ «это один и тот же аппарат».
 *
 * ВЕРСИЯ ОС В КЛЮЧ НЕ ВХОДИТ, И ЭТО ГЛАВНОЕ. Сначала она была частью ключа — чтобы
 * не склеить два одинаковых телефона в одной семье. Замер по боевым данным показал,
 * что это отсекало как раз тот случай, ради которого всё затевалось: приложения
 * ставят в разное время, между установками телефон успевает обновиться, и один
 * айфон с тремя приложениями на трёх слотах (у каждого своя версия iOS) подсказку
 * НЕ вызывал. Без версии ОС в ключе таких людей и лишних слотов нашлось заметно
 * больше.
 *
 * Цена послабления: два одинаковых телефона с одинаковой моделью действительно
 * попадут в одну группу. Но текст подсказки — предположение («похоже»), экран
 * ничего не удаляет сам, и человек, глядя на список приложений, разберётся сам.
 * Промолчать там, где слоты правда съедены, — ошибка дороже.
 *
 * Без модели ключ собирается из платформы и версии ОС: это слабее, поэтому нужны
 * оба поля. Две записи, где не известно ничего, одним аппаратом не объявляются.
 */
export function deviceKey(device: DeviceResponse): string | null {
  const model = (device.device_model ?? "").trim().toLowerCase();
  const platform = (device.platform ?? "").trim().toLowerCase();
  if (model) return platform ? `${model}|${platform}` : model;

  const os = (device.os_version ?? "").trim().toLowerCase();
  return platform && os ? `?|${platform}|${os}` : null;
}

export interface SameDeviceGroup {
  /** Как назвать аппарат человеку. */
  label: string;
  devices: DeviceResponse[];
  /** Приложения, занявшие слоты, в порядке подключения. */
  apps: string[];
}

/** Группы из двух и более слотов, похожих на один аппарат. Пусто — всё в порядке. */
export function sameDeviceGroups(devices: DeviceResponse[]): SameDeviceGroup[] {
  const buckets = new Map<string, DeviceResponse[]>();
  for (const d of devices) {
    const key = deviceKey(d);
    if (!key) continue;
    const list = buckets.get(key);
    if (list) list.push(d);
    else buckets.set(key, [d]);
  }

  const out: SameDeviceGroup[] = [];
  for (const list of buckets.values()) {
    const first = list[0];
    if (list.length < 2 || !first) continue;
    const label = [first.device_model, first.os_version ? `${first.platform ?? ""} ${first.os_version}`.trim() : first.platform]
      .filter(Boolean)
      .join(" · ");
    out.push({
      label: label || first.hwid,
      devices: list,
      apps: list.map((d) => appFromUserAgent(d.user_agent) ?? "—"),
    });
  }
  // Сначала те, где занято больше слотов: с них и стоит начинать уборку.
  return out.sort((a, b) => b.devices.length - a.devices.length);
}

/** Сколько слотов можно освободить, оставив по одному на каждый аппарат. */
export function freeableSlots(groups: SameDeviceGroup[]): number {
  return groups.reduce((sum, g) => sum + g.devices.length - 1, 0);
}
