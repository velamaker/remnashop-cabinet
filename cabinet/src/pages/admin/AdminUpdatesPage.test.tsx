import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import type { Appearance } from "@/api/appearance";
import { BOT_CAPABILITIES, type BotCap } from "@/lib/botCapabilities";

// «Обновления»: владелец, обновивший только кабинет, ищет здесь, куда пропали
// функции. Карточка перечисляет скрытое и команду обновления бота — только по
// свежему оформлению (в кэше списка возможностей нет) и не поверх чужого бэкенда.

vi.mock("@/api/admin", () => ({
  updatesAdminApi: {
    get: () =>
      Promise.resolve({ current: "1.3.8", latest: "1.3.8", update_available: false, repo: "x/y", items: [] }),
  },
}));

let branding: { appearance: Appearance | null; loaded: boolean } = { appearance: null, loaded: false };
vi.mock("@/contexts/BrandingContext", () => ({ useBranding: () => branding }));

const { default: AdminUpdatesPage } = await import("./AdminUpdatesPage");

const ALL = Object.keys(BOT_CAPABILITIES) as BotCap[];
const card = () => screen.queryByTestId("bot-behind-cabinet");

async function show() {
  render(<AdminUpdatesPage />);
  await screen.findByText(/Установлена последняя версия/);
}

beforeEach(() => {
  branding = { appearance: null, loaded: false };
});
afterEach(() => cleanup());

describe("карточка «Кабинет новее бота»", () => {
  it("бот 1.3.8 — перечислено всё скрытое и команда обновления бота", async () => {
    branding = { appearance: { brand_name: "X" } as Appearance, loaded: true };
    await show();
    const el = card();
    expect(el).not.toBeNull();
    for (const cap of ALL) expect(el!.textContent).toContain(BOT_CAPABILITIES[cap].label);
    expect(el!.textContent).toContain("./update.sh --with-bot");
  });

  it("не хватает одного токена — ровно одна строка", async () => {
    const caps = ALL.filter((c) => c !== "digest_email");
    branding = { appearance: { brand_name: "X", bot_capabilities: caps } as Appearance, loaded: true };
    await show();
    await waitFor(() => expect(card()?.querySelectorAll("li").length).toBe(1));
    expect(card()!.textContent).toContain(BOT_CAPABILITIES.digest_email.label);
  });

  it("бот умеет всё, чужой бэкенд или оформление только из кэша — карточки нет", async () => {
    for (const b of [
      { appearance: { brand_name: "X", bot_capabilities: [...ALL] } as Appearance, loaded: true },
      { appearance: { brand_name: "X", features: {} } as Appearance, loaded: true },
      { appearance: { brand_name: "X" } as Appearance, loaded: false },
    ]) {
      branding = b;
      await show();
      expect(card()).toBeNull();
      cleanup();
    }
  });
});
