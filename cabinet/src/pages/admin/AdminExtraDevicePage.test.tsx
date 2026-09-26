import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";
import { formatDateTime } from "@/lib/format";

// Страница настроек денежной функции: что именно уходит на бэкенд и что видит
// владелец, пока цена не задана. Продажи по умолчанию ВЫКЛЮЧЕНЫ — выкатка образа
// сама по себе брать деньги не должна.
const getMock = vi.fn();
const updateMock = vi.fn();
const fullGetMock = vi.fn();
const fullUpdateMock = vi.fn();
vi.mock("@/api/admin", () => ({
  extraDeviceAdminApi: {
    get: () => getMock(),
    update: (body: unknown) => updateMock(body),
  },
  deviceFullAdminApi: {
    get: () => fullGetMock(),
    update: (body: unknown) => fullUpdateMock(body),
  },
}));

// Секция «все места заняты» рисуется только новому боту — по токену device_full.
const branding = { caps: ["extra_device", "device_full"] };
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ can: (key: string) => branding.caps.includes(key) }),
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
  fullGetMock.mockResolvedValue({
    config: { enabled: false, cooldown_days: 7 },
    baselined: false,
    full_now: 0,
    last_run: null,
  });
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

describe("AdminExtraDevicePage: «все места заняты»", () => {
  const full = (over: Record<string, unknown> = {}) => ({
    config: { enabled: false, cooldown_days: 7 },
    baselined: true,
    full_now: 21,
    last_run: { at: "2026-09-26T12:00:00+00:00", baseline: false, sent: 2, failed: 0 },
    ...over,
  });

  beforeEach(() => {
    fullGetMock.mockReset();
    fullUpdateMock.mockReset();
    branding.caps = ["extra_device", "device_full"];
  });

  it("по умолчанию выключено; включение уходит на бэкенд ровно тем, что показано", async () => {
    getMock.mockResolvedValue(answer());
    fullGetMock.mockResolvedValue(full());
    fullUpdateMock.mockResolvedValue(full({ config: { enabled: true, cooldown_days: 7 } }));
    renderPage();
    const toggle = await screen.findByRole("checkbox", { name: rx("adm.devfull.enabled_label") });
    expect((toggle as HTMLInputElement).checked).toBe(false);
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: ru("adm.devfull.save") }));
    await waitFor(() => expect(fullUpdateMock).toHaveBeenCalledWith({ enabled: true, cooldown_days: 7 }));
    expect(await screen.findByRole("status")).toHaveTextContent(ru("adm.devfull.saved"));
  });

  // «Сейчас заняты у 0 чел.» при выключенной функции читалось как «ни у кого не
  // заняты»; на деле число считает проход крона, а он идёт, только когда включено.
  describe("сколько людей упёрлись в лимит — только когда посчитано", () => {
    const AT = "2026-09-26T12:00:00+00:00";
    const counted = () => ru("adm.devfull.full_now", { n: 21, at: formatDateTime(AT) });

    it("выключено — нейтральное «посчитаем», а не «заняты у N чел.»", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full({ full_now: 0, last_run: null }));
      renderPage();
      await screen.findByText(ru("adm.devfull.not_counted"));
      expect(document.body.textContent).not.toMatch(/заняты у 0 чел/);
    });

    it("выключено после прошлых проходов — старое число не показываем", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full());
      renderPage();
      await screen.findByText(ru("adm.devfull.not_counted"));
      expect(document.body.textContent).not.toContain(counted());
      expect(document.body.textContent).not.toContain(ru("adm.devfull.last_run", { sent: 2, failed: 0 }));
    });

    it("включено, но прохода ещё не было — тоже «посчитаем»", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full({ config: { enabled: true, cooldown_days: 7 }, full_now: 0, last_run: null }));
      renderPage();
      await screen.findByText(ru("adm.devfull.not_counted"));
    });

    it("тумблер включили, но не сохранили — числа ещё нет", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full());
      renderPage();
      fireEvent.click(await screen.findByRole("checkbox", { name: rx("adm.devfull.enabled_label") }));
      expect(screen.getByText(ru("adm.devfull.not_counted"))).toBeTruthy();
    });

    it("включено и посчитано — число со временем прохода и итог прохода", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full({ config: { enabled: true, cooldown_days: 7 } }));
      renderPage();
      await waitFor(() => expect(document.body.textContent).toContain(counted()));
      expect(document.body.textContent).toContain(ru("adm.devfull.last_run", { sent: 2, failed: 0 }));
      expect(screen.queryByText(ru("adm.devfull.not_counted"))).toBeNull();
    });

    it("сохранили включение — число появляется из ответа сохранения", async () => {
      getMock.mockResolvedValue(answer());
      fullGetMock.mockResolvedValue(full());
      fullUpdateMock.mockResolvedValue(full({ config: { enabled: true, cooldown_days: 7 } }));
      renderPage();
      fireEvent.click(await screen.findByRole("checkbox", { name: rx("adm.devfull.enabled_label") }));
      fireEvent.click(screen.getByRole("button", { name: ru("adm.devfull.save") }));
      await waitFor(() => expect(document.body.textContent).toContain(counted()));
    });
  });

  it("старый бот без токена — секции нет и за ручкой не ходим", async () => {
    branding.caps = ["extra_device"];
    getMock.mockResolvedValue(answer());
    renderPage();
    await screen.findByText(ru("adm.extradevice.title"));
    expect(fullGetMock).not.toHaveBeenCalled();
    expect(screen.queryByText(ru("adm.devfull.title"))).toBeNull();
  });
});
