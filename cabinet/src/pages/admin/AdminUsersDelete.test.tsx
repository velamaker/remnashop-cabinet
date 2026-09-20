import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { I18nProvider } from "@/i18n/I18nContext";
import { STORAGE_KEY } from "@/i18n/config";
import { translate } from "@/i18n/translate";

/**
 * Удаление человека из карточки админки.
 *
 * ЧТО ЗАПЕРТО ЗДЕСЬ. В карточке давно были «Отключить» и «Удалить», но обе про
 * ПОДПИСКУ — сам человек оставался в базе навсегда. Серые кнопки админы читали
 * как поломку («удаление не активное»), а дубли из панели убирать было нечем.
 * Теперь удаление человека есть, и опасность у него настоящая — поэтому тест
 * держит предохранители: слово подтверждения, недоступность владельца, себя и
 * тех, кому нарезали лишь часть разделов, и честный ответ о том, что вышло:
 * за платившим остаётся обезличенная запись, и админ обязан это увидеть.
 */

const get = vi.fn();
const remove = vi.fn();

vi.mock("@/api/admin", () => ({
  usersAdminApi: {
    list: () =>
      Promise.resolve({
        items: [
          {
            id: 42, telegram_id: 500100, auth_type: "telegram", email: "kto@to.ru",
            name: "Кто-то", username: "kto", role: 1, is_blocked: false,
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
        total: 1,
      }),
    get: (id: number) => get(id),
    remove: (id: number, confirm: string) => remove(id, confirm),
    logins: () => Promise.resolve({ events: [], total: 0 }),
    referrals: () => Promise.resolve({ referrer: null, counts: { first: 0, second: 0 }, first: [], second: [] }),
    trafficByNode: () => Promise.resolve({ nodes: [], days: 30 }),
    block: vi.fn(), setTrial: vi.fn(), changeRole: vi.fn(), setDiscount: vi.fn(),
    bulkAction: vi.fn(), exportXlsx: vi.fn(),
  },
  subscriptionsAdminApi: {
    getUser: () => Promise.resolve({ current: null, history: [] }),
    devices: () => Promise.resolve({ current_count: 0, max_count: 0, devices: [] }),
    squads: () => Promise.resolve({ squads: [] }),
  },
  plansAdminApi: { list: () => Promise.resolve({ items: [], total: 0 }) },
  grantsAdminApi: {
    catalog: () => Promise.resolve({ sections: [], presets: [] }),
    get: () =>
      Promise.resolve({
        user_id: 42, role: 1, has_grant: false, full_access: false, can_write: false,
        sections: [], expires_at: null, granted_by: null,
        effective: { allowed: false, full_access: false, can_write: false, sections: [], source: "none" },
      }),
    set: vi.fn(),
    revoke: vi.fn(),
  },
}));

type Auth = { isOwner: boolean; isReadonlyAdmin: boolean; fullAccess: boolean; canSection: (k: string) => boolean };
let auth: Auth;
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ can: () => true, appearance: { brand_name: "X" } }),
}));
vi.mock("./AdminUsersBulk", () => ({
  BulkDaysDialog: () => null,
  BulkJobsPanel: () => null,
  BulkMessageDialog: () => null,
}));

const { default: AdminUsersPage } = await import("./AdminUsersPage");

const ru = (key: string, vars?: Record<string, string | number>) => translate(key, vars, "ru");

function detail(role = 1) {
  return {
    user: {
      id: 42, telegram_id: 500100, auth_type: "telegram", email: "kto@to.ru",
      is_email_verified: true, name: "Кто-то", username: "kto", role,
      language: "ru", is_blocked: false, is_bot_blocked: false, is_trial_available: false,
      personal_discount: 0, purchase_discount: 0, points: 0, referral_code: "abc123",
      created_at: "2026-01-01T00:00:00Z",
    },
    transactions: [],
    history: [],
  };
}

const FULL: Auth = { isOwner: true, isReadonlyAdmin: false, fullAccess: true, canSection: () => true };

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, "ru");
  auth = { ...FULL };
  get.mockResolvedValue(detail());
  remove.mockReset();
  remove.mockResolvedValue({ success: true, mode: "purged", panel_accounts_removed: 1 });
  vi.spyOn(window, "alert").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

/** Открывает карточку человека и вкладку профиля — там живёт удаление. */
async function openProfile() {
  render(
    <I18nProvider>
      <AdminUsersPage />
    </I18nProvider>,
  );
  // Имя рисуется дважды: таблица для монитора и карточки для телефона живут в
  // разметке одновременно, видимость разводит CSS, а jsdom их не различает.
  fireEvent.click((await screen.findAllByText("Кто-то"))[0]!);
  fireEvent.click(await screen.findByText(ru("adm.users.tab_info")));
}

const deleteButton = () => screen.queryByText(ru("adm.users.del_btn"));
const confirmButton = () =>
  screen.getByText(ru("adm.users.del_forever")).closest("button") as HTMLButtonElement;

describe("кому вообще показываем удаление", () => {
  it("админу с полным доступом — показываем", async () => {
    await openProfile();
    expect(deleteButton()).toBeTruthy();
  });

  it("тому, кому нарезали разделы, — нет: удаление человека не работа поддержки", async () => {
    auth = { ...FULL, isOwner: false, fullAccess: false };
    await openProfile();
    expect(deleteButton()).toBeNull();
  });

  it("админу только для просмотра — нет", async () => {
    auth = { ...FULL, isReadonlyAdmin: true };
    await openProfile();
    expect(deleteButton()).toBeNull();
  });

  it("владельца не удалить", async () => {
    get.mockResolvedValue(detail(5));
    await openProfile();
    expect(deleteButton()).toBeNull();
  });
});

describe("подтверждение и ответ", () => {
  it("пока слово не набрано — кнопка заперта, запрос не уходит", async () => {
    await openProfile();
    fireEvent.click(deleteButton()!);
    expect(confirmButton().hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText(/УДАЛИТЬ/), { target: { value: "удали" } });
    expect(confirmButton().hasAttribute("disabled")).toBe(true);
    expect(remove).not.toHaveBeenCalled();
  });

  it("слово набрано — удаляем, слово уезжает на сервер", async () => {
    await openProfile();
    fireEvent.click(deleteButton()!);
    fireEvent.change(screen.getByLabelText(/УДАЛИТЬ/), { target: { value: "удалить" } });
    expect(confirmButton().hasAttribute("disabled")).toBe(false);
    fireEvent.click(confirmButton());

    await waitFor(() => expect(remove).toHaveBeenCalledWith(42, "удалить"));
  });

  it("за платившим осталась обезличенная запись — говорим об этом, а не «удалено»", async () => {
    remove.mockResolvedValue({ success: true, mode: "anonymized", panel_accounts_removed: 1 });
    const alerted = vi.spyOn(window, "alert").mockImplementation(() => {});
    await openProfile();
    fireEvent.click(deleteButton()!);
    fireEvent.change(screen.getByLabelText(/УДАЛИТЬ/), { target: { value: "УДАЛИТЬ" } });
    fireEvent.click(confirmButton());

    await waitFor(() => expect(alerted).toHaveBeenCalledWith(ru("adm.users.del_done_anon")));
  });
});

describe("слово подтверждения знает язык админки", () => {
  it("по-русски просят УДАЛИТЬ, по-английски DELETE", () => {
    expect(translate("adm.users.del_word", undefined, "ru")).toBe("УДАЛИТЬ");
    expect(translate("adm.users.del_word", undefined, "en")).toBe("DELETE");
  });
});
