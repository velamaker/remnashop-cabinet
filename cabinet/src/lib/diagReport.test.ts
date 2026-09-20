import { describe, it, expect } from "vitest";
import {
  buildTicketBody,
  buildTicketSubject,
  browserName,
  deviceChipLabel,
  deviceChoiceLabel,
  fixMojibake,
  guessPlatform,
  techLine,
} from "./diagReport";
import type { DeviceResponse } from "@/types/api";

/**
 * «Паспорт обращения» из самодиагностики.
 *
 * ЗАЧЕМ ТЕСТ. Владелец получает тикет один раз: если в тело не попал аппарат или
 * список «что не работает», переписка уходит на второй круг, а половина людей на
 * уточнение уже не отвечает. Здесь заперто ровно то, что должно доехать до
 * Telegram-уведомления.
 */

function dev(over: Partial<DeviceResponse> = {}): DeviceResponse {
  return {
    hwid: "abc",
    platform: "ios",
    device_model: "iPhone 14",
    os_version: "iOS 17.4",
    user_agent: "v2RayTun/2.1/ios/abc",
    created_at: null,
    updated_at: null,
    ...over,
  };
}

describe("подпись устройства", () => {
  it("модель, приложение и версия ОС в одну строку", () => {
    expect(deviceChoiceLabel(dev(), "Неизвестно")).toBe("iPhone 14 · v2RayTun · iOS 17.4");
  });

  it("модель и платформа совпали — не дублируем", () => {
    expect(deviceChoiceLabel(dev({ device_model: "Android", platform: "Android", user_agent: null, os_version: null }), "Неизвестно")).toBe(
      "Android",
    );
  });

  it("панель не знает ничего — подставляем «неизвестно», а не пустоту", () => {
    expect(
      deviceChoiceLabel(dev({ device_model: null, platform: null, os_version: null, user_agent: null }), "Неизвестно"),
    ).toBe("Неизвестно");
  });

  it("кракозябры из панели чиним: человек выбирает аппарат, а не «Ð½Ð¾ÑƒÑ‚»", () => {
    // Windows-приложение прислало имя компьютера UTF-8, прочитанным как latin-1.
    const broken = "Ð½Ð¾ÑƒÑ‚_x86_64";
    expect(fixMojibake(broken)).toBe("ноут_x86_64");
    expect(deviceChoiceLabel(dev({ device_model: broken, user_agent: "Happ/1/win", os_version: null }), "?")).toBe(
      "ноут_x86_64 · Happ",
    );
  });

  it("нормальные имена не трогаем", () => {
    expect(fixMojibake("iPhone 14 Pro")).toBe("iPhone 14 Pro");
    expect(fixMojibake("ноутбук Саши")).toBe("ноутбук Саши");
    expect(fixMojibake("Ñ")).toBe("Ñ"); // не разбирается как UTF-8 — оставляем
  });

  it("на кнопке подпись короткая, в тикете — полная", () => {
    const long = dev({ device_model: "iPad Pro (12.9-inch) (6th generation)", user_agent: "INCY/2/ios", os_version: "18.7.8" });
    expect(deviceChipLabel(long, "?")).toHaveLength(34);
    expect(deviceChipLabel(long, "?").endsWith("…")).toBe(true);
    expect(deviceChoiceLabel(long, "?")).toBe("iPad Pro (12.9-inch) (6th generation) · INCY · 18.7.8");
  });

  it("короткое имя на кнопке не режем", () => {
    expect(deviceChipLabel(dev(), "?")).toBe("iPhone 14 · v2RayTun");
  });
});

describe("угадывание платформы", () => {
  it("подсказка Telegram важнее user-agent браузера", () => {
    // В мини-аппе UA — это UA телеграмовского WebView, там бывает что угодно.
    expect(guessPlatform("Mozilla/5.0 (Linux; Android 13)", "ios")).toBe("ios");
  });

  it("iPhone, Android, Windows, Mac — по user-agent", () => {
    expect(guessPlatform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4)")).toBe("ios");
    expect(guessPlatform("Mozilla/5.0 (Linux; Android 13; SM-G991B)")).toBe("android");
    expect(guessPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(guessPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")).toBe("macos");
  });

  it("телевизор не превращается в Android", () => {
    expect(guessPlatform("Mozilla/5.0 (Linux; Android 9; SMART-TV; Android TV)")).toBe("tv");
  });

  it("пусто — «другое», а не ложная догадка", () => {
    expect(guessPlatform(null)).toBe("other");
    expect(guessPlatform("")).toBe("other");
  });
});

describe("тех-строка", () => {
  it("читается на любом языке обращения: ключи латиницей", () => {
    const line = techLine({
      platform: "ios",
      app: "v2RayTun",
      problems: ["instagram", "youtube"],
      miniApp: true,
      ua: "Mozilla/5.0 (iPhone) Safari/605.1",
    });
    expect(line).toBe("platform=ios · app=v2RayTun · problems=instagram,youtube · browser=Safari · mini-app");
  });

  it("без приложения и вне мини-аппы — без пустых кусков", () => {
    expect(techLine({ platform: "windows", problems: [], ua: "Chrome/120" })).toBe(
      "platform=windows · browser=Chrome",
    );
  });

  it("браузер определяется, не путая Chrome с Edge", () => {
    expect(browserName("Mozilla/5.0 Chrome/120 Edg/120")).toBe("Edge");
    expect(browserName("Mozilla/5.0 Chrome/120 Safari/537")).toBe("Chrome");
    expect(browserName(null)).toBe("?");
  });
});

describe("тело тикета", () => {
  const labels = {
    device: "Устройство",
    problems: "Не работает",
    comment: "Комментарий",
    checks: "Результаты самопроверки",
  };

  it("ответы человека идут первыми, машинная часть — последней", () => {
    const body = buildTicketBody({
      labels,
      device: "iPhone 14 · v2RayTun",
      problems: ["Instagram", "YouTube"],
      comment: "  подключается, но ничего не грузит  ",
      summary: "✅ Подписка активна\n✅ Серверы работают",
      tech: "platform=ios · problems=instagram,youtube",
    });
    expect(body).toBe(
      "Устройство: iPhone 14 · v2RayTun\n" +
        "Не работает: Instagram, YouTube\n" +
        "Комментарий: подключается, но ничего не грузит\n\n" +
        "Результаты самопроверки:\n✅ Подписка активна\n✅ Серверы работают\n\n" +
        "platform=ios · problems=instagram,youtube",
    );
  });

  it("пустой комментарий не оставляет висячую строку", () => {
    const body = buildTicketBody({
      labels,
      device: "Android",
      problems: ["Игры"],
      comment: "   ",
      summary: "",
      tech: "platform=android",
    });
    expect(body).toBe("Устройство: Android\nНе работает: Игры\n\nplatform=android");
  });
});

describe("тема тикета", () => {
  it("называет, что именно не работает", () => {
    expect(buildTicketSubject("VPN не работает — автодиагностика", ["Instagram", "YouTube"])).toBe(
      "VPN не работает — автодиагностика: Instagram, YouTube",
    );
  });

  it("без выбора — остаётся базовая тема", () => {
    expect(buildTicketSubject("VPN не работает", [])).toBe("VPN не работает");
  });

  it("длинный список режется под ограничение ручки (200)", () => {
    const subject = buildTicketSubject("VPN не работает", Array.from({ length: 40 }, (_, i) => `Сервис ${i}`));
    expect(subject.length).toBeLessThanOrEqual(200);
    expect(subject.endsWith("…")).toBe(true);
  });
});
