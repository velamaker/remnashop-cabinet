import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type { AdminAppearance, Appearance } from "@/api/appearance";
import type { FeatureKey } from "@/lib/features";

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

const TOGGLE = /Предлагать тариф побольше/;

async function saveAndGetBody() {
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  return update.mock.calls[0]![0] as Record<string, unknown>;
}

beforeEach(() => {
  update.mockReset();
  update.mockResolvedValue({ ...stored });
});
afterEach(() => cleanup());

describe("тумблер «Нужно больше устройств?»", () => {
  it("бот 1.3.8 — тумблера нет, поле в сохранение не уходит", async () => {
    appearance = { brand_name: "X" } as Appearance;
    render(<AdminCabinetPage />);
    await screen.findByText("Доступ и язык");
    expect(screen.queryByText(TOGGLE)).toBeNull();
    const body = await saveAndGetBody();
    expect("device_upsell_enabled" in body).toBe(false);
  });

  it("бот с токеном — тумблер есть и сохраняется", async () => {
    appearance = { brand_name: "X", bot_capabilities: ["device_upsell"] } as Appearance;
    render(<AdminCabinetPage />);
    fireEvent.click(await screen.findByLabelText(TOGGLE));
    const body = await saveAndGetBody();
    expect(body.device_upsell_enabled).toBe(false);
  });

  it("чужой бэкенд выключил возможность — тумблера нет (как раньше)", async () => {
    appearance = { brand_name: "X", features: { device_upsell: false } as Record<string, boolean> } as Appearance;
    render(<AdminCabinetPage />);
    await screen.findByText("Доступ и язык");
    expect(screen.queryByText(TOGGLE)).toBeNull();
    const body = await saveAndGetBody();
    expect("device_upsell_enabled" in body).toBe(false);
  });
});
