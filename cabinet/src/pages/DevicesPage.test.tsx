import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";
import { device, devicesOf, sub } from "@/test/offersFixtures";

/**
 * «Устройства» по ссылке ?buy=1 из сообщения бота «все места заняты».
 *
 * ЧТО ЗАПЕРТО. Страница прокручивается к предложению места ОДИН раз — после первой
 * загрузки. Раньше прокрутка срабатывала на каждое обновление списка: удалил
 * устройство или перечитал список — и страницу утягивало от строки, с которой
 * человек сейчас работает.
 */

const devices = vi.fn();
const deleteDevice = vi.fn();
const deleteAllDevices = vi.fn();
vi.mock("@/api/subscription", () => ({
  subscriptionApi: {
    current: () => Promise.resolve(sub()),
    devices: () => devices(),
    deleteDevice: (hwid: string) => deleteDevice(hwid),
    deleteAllDevices: () => deleteAllDevices(),
  },
}));
// Соседние блоки ходят в свои ручки; от карточки докупки нужен только её «перечитать».
vi.mock("@/components/ConnectGuide", () => ({ ConnectGuide: () => null }));
vi.mock("@/components/ExtraDevicesPanel", () => ({ ExtraDevicesPanel: () => null }));
vi.mock("@/components/DeviceUpsellCard", () => ({
  DeviceUpsellCard: ({ onChanged }: { onChanged: () => void }) => (
    <button type="button" onClick={onChanged}>
      upsell-refresh
    </button>
  ),
}));

const { default: DevicesPage } = await import("./DevicesPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const scroll = vi.fn();
const PHONES = () => [device({ device_model: "Phone A" }), device({ device_model: "Phone B" })];

function renderPage(url: string) {
  window.history.replaceState({}, "", url);
  return render(
    <I18nProvider>
      <DevicesPage />
    </I18nProvider>,
  );
}

/** Кнопка удаления в строке устройства (иконка корзины, без подписи). */
function deleteButtonOf(model: string): HTMLButtonElement {
  const row = screen.getByText(model).closest("div.flex.items-center") as HTMLElement;
  return row.querySelector("button") as HTMLButtonElement;
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  devices.mockReset();
  deleteDevice.mockReset();
  deleteAllDevices.mockReset();
  scroll.mockReset();
  devices.mockResolvedValue(devicesOf(PHONES(), 2));
  deleteDevice.mockResolvedValue({ success: true });
  deleteAllDevices.mockResolvedValue({ success: true });
  Element.prototype.scrollIntoView = scroll;
});
afterEach(() => {
  cleanup();
  // В jsdom прокрутки нет — возвращаем как было, чтобы не влиять на другие тесты.
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  window.history.replaceState({}, "", "/");
});

describe("«Устройства» по ?buy=1: прокрутка к докупке — один раз", () => {
  it("после первой загрузки прокручивает к блоку докупки", async () => {
    renderPage("/devices?buy=1");
    await screen.findByText("Phone A");
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    expect(scroll.mock.contexts[0]).toBe(document.getElementById("buy-slot"));
  });

  it("удаление устройства и перечитывание списка страницу не утягивают", async () => {
    renderPage("/devices?buy=1");
    await screen.findByText("Phone A");
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));

    // Удалили устройство — список обновился на месте.
    await act(async () => {
      fireEvent.click(deleteButtonOf("Phone A"));
    });
    expect(deleteDevice).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText("Phone A")).toBeNull());
    expect(scroll).toHaveBeenCalledTimes(1);

    // Докупили место — список перечитан целиком (с загрузкой).
    devices.mockResolvedValue(devicesOf([device({ device_model: "Phone C" })], 3));
    await act(async () => {
      fireEvent.click(screen.getByText("upsell-refresh"));
    });
    await screen.findByText("Phone C");
    expect(devices).toHaveBeenCalledTimes(2);
    expect(scroll).toHaveBeenCalledTimes(1);

    // «Удалить все» — тоже перечитывание.
    await act(async () => {
      fireEvent.click(screen.getByText(ru("devices.clearAll")));
    });
    await waitFor(() => expect(devices).toHaveBeenCalledTimes(3));
    expect(scroll).toHaveBeenCalledTimes(1);
  });

  it("без ?buy=1 никуда не прокручивает", async () => {
    renderPage("/devices");
    await screen.findByText("Phone A");
    expect(scroll).not.toHaveBeenCalled();
  });
});
