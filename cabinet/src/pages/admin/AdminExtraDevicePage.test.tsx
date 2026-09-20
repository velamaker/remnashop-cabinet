import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// Страница настроек денежной функции: что именно уходит на бэкенд и что видит
// владелец, пока цена не задана. Продажи по умолчанию ВЫКЛЮЧЕНЫ — выкатка образа
// сама по себе брать деньги не должна.
const getMock = vi.fn();
const updateMock = vi.fn();
vi.mock("@/api/admin", () => ({
  extraDeviceAdminApi: {
    get: () => getMock(),
    update: (body: unknown) => updateMock(body),
  },
}));

const { AdminExtraDevicePage } = await import("./AdminExtraDevicePage");

// Подписи страница больше не хранит в коде: они приходят из словаря по ключам
// adm.extradevice.*. Тест сверяется с тем же словарём (и держит админку на
// русском), иначе он проверял бы не интерфейс, а копию строки.
const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");
const rx = (key: string, vars?: Record<string, string | number>) =>
  new RegExp(ru(key, vars).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminExtraDevicePage />
    </I18nProvider>,
  );

const config = (over: Record<string, unknown> = {}) => ({
  enabled: false,
  price_rub_30d: 100,
  min_amount_rub: 10,
  min_days_left: 3,
  max_extra: 2,
  remove_excess_devices: true,
  notify_users: true,
  notify_admins: true,
  ...over,
});

const answer = (over: Record<string, unknown> = {}) => ({
  config: config(),
  effective_enabled: false,
  hint: [{ from_devices: 1, to_devices: 2, diff_30d_rub: 150, traffic_diff_gb: 50 }],
  summary: { applied_30d: 0, amount_30d: 0, active_slots: 0, credited_open: 0 },
  ...over,
});

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  getMock.mockReset();
  updateMock.mockReset();
  getMock.mockResolvedValue(answer());
});
afterEach(cleanup);

describe("AdminExtraDevicePage", () => {
  it("цена владельца видна, продажи по умолчанию выключены", async () => {
    renderPage();
    const price = (await screen.findByLabelText(
      rx("adm.extradevice.price_label"),
    )) as HTMLInputElement;
    expect(price.value).toBe("100");
    const toggle = screen.getByLabelText(rx("adm.extradevice.sell_label")) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });

  it("включили без цены — предупреждение, что продажи всё равно закрыты", async () => {
    getMock.mockResolvedValue(answer({ config: config({ enabled: true, price_rub_30d: null }) }));
    renderPage();
    await waitFor(() =>
      expect(document.body.textContent).toContain(ru("adm.extradevice.no_price_warn")),
    );
  });

  it("сохранение шлёт числа, а пустая цена уходит как null", async () => {
    updateMock.mockResolvedValue({ config: config({ price_rub_30d: null }), effective_enabled: false });
    renderPage();
    const price = (await screen.findByLabelText(
      rx("adm.extradevice.price_label"),
    )) as HTMLInputElement;
    fireEvent.change(price, { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(rx("adm.extradevice.max_extra_label")), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: ru("adm.extradevice.save") }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    const body = updateMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(body.price_rub_30d).toBeNull();
    expect(body.max_extra).toBe(3);
    await waitFor(() =>
      expect(document.body.textContent).toContain(ru("adm.extradevice.saved_closed")),
    );
  });

  it("отключение устройств включено (решение владельца «отключить и предложить снова»)", async () => {
    renderPage();
    const toggle = (await screen.findByLabelText(
      rx("adm.extradevice.remove_label"),
    )) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
  });

  it("подсказка о шаге тарифов показывает и трафик — цену ставит человек, не формула", async () => {
    renderPage();
    await waitFor(() => expect(document.body.textContent).toContain("150 ₽"));
    expect(document.body.textContent).toContain(ru("adm.extradevice.hint_traffic", { gb: "+50" }));
  });
});
