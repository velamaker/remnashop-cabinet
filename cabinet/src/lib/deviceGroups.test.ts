import { describe, it, expect } from "vitest";
import {
  appFromUserAgent,
  deviceKey,
  sameDeviceGroups,
  freeableSlots,
} from "./deviceGroups";
import type { DeviceResponse } from "@/types/api";

// Записи ровно в том виде, в каком их отдаёт панель: форматы сняты с боевых
// данных, а не выдуманы.
function dev(p: Partial<DeviceResponse>): DeviceResponse {
  return {
    hwid: Math.random().toString(36).slice(2),
    platform: "iOS",
    device_model: "iPhone 12",
    os_version: "18.6.2",
    user_agent: "Happ/5.7.0/ios/1000000000001",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    ...p,
  } as DeviceResponse;
}

describe("имя приложения из user-agent", () => {
  it("разбирает формат панели", () => {
    expect(appFromUserAgent("Happ/5.7.0/ios/1000000000001")).toBe("Happ");
    expect(appFromUserAgent("INCY/2.6.1/ios CFNetwork/3860.700.2 Darwin/25.0")).toBe("INCY");
    expect(appFromUserAgent("Happ/4.1.0/AndroidTV/10000000000000000001")).toBe("Happ");
  });

  it("голое имя без версии тоже имя", () => {
    // Такое реально встречается: запись с user-agent «xray».
    expect(appFromUserAgent("xray")).toBe("xray");
  });

  it("пусто — значит неизвестно, а не пустая строка", () => {
    expect(appFromUserAgent("")).toBeNull();
    expect(appFromUserAgent("   ")).toBeNull();
    expect(appFromUserAgent(null)).toBeNull();
    expect(appFromUserAgent(undefined)).toBeNull();
  });
});

describe("когда слоты занял один аппарат", () => {
  it("два приложения на одном телефоне — это группа", () => {
    // Ровно тот случай, ради которого всё: Happ и INCY на одном айфоне.
    const groups = sameDeviceGroups([
      dev({ user_agent: "Happ/5.7.0/ios/1" }),
      dev({ user_agent: "INCY/2.6.1/ios CFNetwork/3860" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.apps).toEqual(["Happ", "INCY"]);
    expect(groups[0]!.label).toContain("iPhone 12");
  });

  it("одно и то же приложение дважды — тоже группа", () => {
    // Переустановка оставляет мёртвый слот; на бою таких случаев половина.
    const groups = sameDeviceGroups([
      dev({ user_agent: "Happ/4.3.0/Android/1", platform: "Android", device_model: "RMX3941", os_version: "15" }),
      dev({ user_agent: "Happ/4.4.1/Android/2", platform: "Android", device_model: "RMX3941", os_version: "15" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.apps).toEqual(["Happ", "Happ"]);
  });

  it("разные телефоны не склеиваются", () => {
    expect(
      sameDeviceGroups([
        dev({ device_model: "iPhone 12" }),
        dev({ device_model: "iPhone 16 Pro" }),
        dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
      ]),
    ).toEqual([]);
  });

  it("та же модель с РАЗНОЙ версией ОС — это один аппарат, который обновился", () => {
    // Сначала здесь было ровно наоборот, и это была ошибка: приложения ставят в
    // разное время, телефон между установками обновляется. Замер по боевым данным
    // показал, что строгое правило пропускало главный случай — один айфон с тремя
    // приложениями на трёх слотах, у каждого своя версия iOS.
    const groups = sameDeviceGroups([
      dev({ os_version: "18.1", user_agent: "v2raytun/1.0/ios" }),
      dev({ os_version: "18.2", user_agent: "INCY/2.6.1/ios" }),
      dev({ os_version: "18.1.1", user_agent: "Happ/5.7.0/ios" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.apps).toEqual(["v2raytun", "INCY", "Happ"]);
    expect(freeableSlots(groups)).toBe(2);
  });

  it("разные модели не склеиваются даже при одинаковой ОС", () => {
    expect(
      sameDeviceGroups([
        dev({ device_model: "iPhone 12", os_version: "18.6.2" }),
        dev({ device_model: "iPhone 16 Pro", os_version: "18.6.2" }),
      ]),
    ).toEqual([]);
  });

  it("записи без модели и ОС не считаются одинаковыми", () => {
    // «Ничего не известно» — это не «одно и то же». Иначе экран обвинял бы
    // человека в лишних слотах на пустом месте.
    expect(
      sameDeviceGroups([
        dev({ device_model: null, os_version: null, platform: "iOS" }),
        dev({ device_model: null, os_version: null, platform: "iOS" }),
      ] as DeviceResponse[]),
    ).toEqual([]);
  });

  it("без модели опора на платформу и ОС — и нужны оба", () => {
    // Слабый признак, поэтому требуем оба поля.
    expect(
      sameDeviceGroups([
        dev({ device_model: null, platform: "iOS", os_version: "18.6.2" }),
        dev({ device_model: null, platform: "iOS", os_version: "18.6.2" }),
      ] as DeviceResponse[]),
    ).toHaveLength(1);
  });

  it("группы идут от самой тяжёлой: с неё и начинать уборку", () => {
    const groups = sameDeviceGroups([
      dev({ device_model: "iPhone 12" }),
      dev({ device_model: "iPhone 12" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "16" }),
    ]);
    expect(groups.map((g) => g.devices.length)).toEqual([3, 2]);
  });

  it("сколько слотов освободится, если оставить по одному на аппарат", () => {
    const groups = sameDeviceGroups([
      dev({ device_model: "iPhone 12" }),
      dev({ device_model: "iPhone 12" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
      dev({ device_model: "RMX3941", platform: "Android", os_version: "15" }),
    ]);
    expect(freeableSlots(groups)).toBe(3);
    expect(freeableSlots([])).toBe(0);
  });

  it("регистр и пробелы не мешают узнать один аппарат", () => {
    expect(
      sameDeviceGroups([
        dev({ device_model: "iPhone 12", platform: "iOS" }),
        dev({ device_model: " IPHONE 12 ", platform: "ios" }),
      ]),
    ).toHaveLength(1);
  });

  it("одно устройство — говорить не о чем", () => {
    expect(sameDeviceGroups([dev({})])).toEqual([]);
    expect(sameDeviceGroups([])).toEqual([]);
  });
});

describe("ключ аппарата", () => {
  it("модель — главный признак; без неё нужны платформа и ОС вместе", () => {
    expect(deviceKey(dev({ device_model: "iPhone 12", os_version: null }) as DeviceResponse)).not.toBeNull();
    expect(deviceKey(dev({ device_model: null, os_version: null }) as DeviceResponse)).toBeNull();
    expect(
      deviceKey(dev({ device_model: null, platform: "iOS", os_version: null }) as DeviceResponse),
    ).toBeNull();
    expect(
      deviceKey(dev({ device_model: null, platform: "iOS", os_version: "18.6" }) as DeviceResponse),
    ).not.toBeNull();
  });

  it("версия ОС в ключ не входит: телефон обновляется, а остаётся тем же", () => {
    const a = deviceKey(dev({ os_version: "26.5" }));
    const b = deviceKey(dev({ os_version: "26.6.1" }));
    expect(a).toBe(b);
  });
});
