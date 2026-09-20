import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import type { Appearance } from "@/api/appearance";
import { BOT_CAPABILITIES, type BotCap } from "@/lib/botCapabilities";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

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

// Экран больше не хранит русский текст в коде: подписи приходят из словаря по
// ключам adm.updates.*. Тест сверяется с тем же словарём (и держит кабинет на
// русском), иначе он проверял бы не интерфейс, а копию строки. Подписи скрытых
// функций — исключение: они из манифеста BOT_CAPABILITIES, его читает update.sh.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

async function show() {
  render(
    <I18nProvider>
      <AdminUpdatesPage />
    </I18nProvider>,
  );
  await screen.findByText(ru("adm.updates.up_to_date", { version: "1.3.8" }));
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
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
