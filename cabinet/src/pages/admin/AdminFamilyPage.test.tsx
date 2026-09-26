import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { setActiveLang, translate } from "@/i18n/translate";

// «Семейные профили» в админке: функция по умолчанию ВЫКЛЮЧЕНА, тариф становится
// семейным только явным действием владельца, пробный — никогда, а «сделать обычным»
// (приостановка и удаление чужих профилей) — только после подтверждения.
const getMock = vi.fn();
const updateMock = vi.fn();
const setTermsMock = vi.fn();
const clearTermsMock = vi.fn();
vi.mock("@/api/admin", () => ({
  familyAdminApi: {
    get: () => getMock(),
    update: (body: unknown) => updateMock(body),
    setTerms: (id: number, body: unknown) => setTermsMock(id, body),
    clearTerms: (id: number) => clearTermsMock(id),
  },
}));

const { AdminFamilyPage } = await import("./AdminFamilyPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

const renderPage = () =>
  render(
    <I18nProvider>
      <AdminFamilyPage />
    </I18nProvider>,
  );

const plans = [
  { id: 1, name: "Базовый", is_active: true, is_trial: false, device_limit: 3, traffic_limit: 100, terms: null },
  {
    id: 2,
    name: "Семейный",
    is_active: true,
    is_trial: false,
    device_limit: 5,
    traffic_limit: 300,
    terms: { max_profiles: 3, devices_per_profile: 2 },
  },
  { id: 3, name: "Пробный", is_active: true, is_trial: true, device_limit: 1, traffic_limit: 10, terms: null },
];

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  setActiveLang("ru");
  for (const m of [getMock, updateMock, setTermsMock, clearTermsMock]) m.mockReset();
  getMock.mockResolvedValue({
    config: { enabled: false, suspend_grace_days: 30 },
    plans,
    summary: { owners: 1, active: 2, suspended: 0, pending: 0, failing: 0 },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AdminFamilyPage", () => {
  it("функция по умолчанию выключена, семейный тариф показан с условиями", async () => {
    renderPage();
    const toggle = (await screen.findByLabelText(ru("adm.family.enabled_label"), { exact: false })) as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    expect(document.body.textContent).toContain(ru("adm.family.terms_row", { profiles: 3, devices: 2 }));
    expect(document.body.textContent).toContain(ru("adm.family.trial_note"));
  });

  it("«Сделать семейным» уходит условиями тарифа", async () => {
    setTermsMock.mockResolvedValue({ plan_id: 1, terms: { max_profiles: 3, devices_per_profile: 3 } });
    renderPage();
    const button = await screen.findByRole("button", { name: ru("adm.family.make_family") });
    fireEvent.click(button);
    await waitFor(() => expect(setTermsMock).toHaveBeenCalledWith(1, { max_profiles: 3, devices_per_profile: 3 }));
  });

  it("пробный тариф сделать семейным нельзя — кнопки нет", async () => {
    renderPage();
    await screen.findByText(ru("adm.family.trial_note"));
    // Одна кнопка «сделать семейным» — у обычного тарифа, у пробного её нет.
    expect(screen.getAllByRole("button", { name: ru("adm.family.make_family") })).toHaveLength(1);
  });

  it("«Сделать обычным» — только после подтверждения", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: ru("adm.family.make_regular") }));
    expect(confirm).toHaveBeenCalled();
    expect(clearTermsMock).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    clearTermsMock.mockResolvedValue({ plan_id: 2, terms: null });
    fireEvent.click(screen.getByRole("button", { name: ru("adm.family.make_regular") }));
    await waitFor(() => expect(clearTermsMock).toHaveBeenCalledWith(2));
  });

  it("вне 1…10 сохранить нельзя", async () => {
    renderPage();
    const inputs = (await screen.findAllByLabelText(ru("adm.family.max_profiles"))) as HTMLInputElement[];
    fireEvent.change(inputs[0]!, { target: { value: "11" } });
    const button = screen.getAllByRole("button", { name: ru("adm.family.make_family") })[0]!;
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(setTermsMock).not.toHaveBeenCalled();
  });
});
