import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type { AdminAppearance, Appearance } from "@/api/appearance";
import type { FeatureKey } from "@/lib/features";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// «Доступ и язык»: тумблер «Нужно больше устройств?». У бота 1.3.8 поля нет в модели
// PUT /admin/appearance — pydantic его молча выбрасывал, кабинет писал «Сохранено»,
// а после перезагрузки галка снова стояла. Кабинет, обновлённый отдельно от бота,
// не показывает тумблер и не шлёт поле; `can` — настоящий canFeature.

const update = vi.fn();
const stored: AdminAppearance = {
  brand_name: null,
  accent: null,
  background: null,
  background_dark: null,
  background_light: null,
  brand_name_resolved: "X",
  device_upsell_enabled: true,
};
vi.mock("@/api/appearance", () => ({
  appearanceAdminApi: {
    get: () => Promise.resolve({ ...stored }),
    update: (data: unknown) => update(data),
  },
}));

let appearance: Appearance = { brand_name: "X" } as Appearance;
vi.mock("@/contexts/BrandingContext", async () => {
  const { canFeature } = await import("@/lib/features");
  return {
    useBranding: () => ({
      refresh: () => Promise.resolve(),
      can: (key: FeatureKey) => canFeature(appearance, key),
    }),
  };
});

const { default: AdminCabinetPage } = await import("./AdminCabinetPage");

// Подписи страницы приходят из словаря по ключам adm.cabinet.*. Тест берёт их оттуда
// же (и держит кабинет на русском), иначе он проверял бы копию строки, а не интерфейс.
const ru = (key: string) => translate(key, undefined, "ru");
const rx = (key: string) => new RegExp(ru(key).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminCabinetPage />
    </I18nProvider>,
  );

const TOGGLE = rx("adm.cabinet.device_upsell");

async function saveAndGetBody() {
  fireEvent.click(screen.getByRole("button", { name: ru("adm.cabinet.save") }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  return update.mock.calls[0]![0] as Record<string, unknown>;
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  update.mockReset();
  update.mockResolvedValue({ ...stored });
});
afterEach(() => cleanup());

describe("тумблер «Нужно больше устройств?»", () => {
  it("бот 1.3.8 — тумблера нет, поле в сохранение не уходит", async () => {
    appearance = { brand_name: "X" } as Appearance;
    renderPage();
    await screen.findByText(ru("adm.cabinet.title"));
    expect(screen.queryByText(TOGGLE)).toBeNull();
    const body = await saveAndGetBody();
    expect("device_upsell_enabled" in body).toBe(false);
  });

  it("бот с токеном — тумблер есть и сохраняется", async () => {
    appearance = { brand_name: "X", bot_capabilities: ["device_upsell"] } as Appearance;
    renderPage();
    fireEvent.click(await screen.findByLabelText(TOGGLE));
    const body = await saveAndGetBody();
    expect(body.device_upsell_enabled).toBe(false);
  });

  it("чужой бэкенд выключил возможность — тумблера нет (как раньше)", async () => {
    appearance = { brand_name: "X", features: { device_upsell: false } as Record<string, boolean> } as Appearance;
    renderPage();
    await screen.findByText(ru("adm.cabinet.title"));
    expect(screen.queryByText(TOGGLE)).toBeNull();
    const body = await saveAndGetBody();
    expect("device_upsell_enabled" in body).toBe(false);
  });
});
